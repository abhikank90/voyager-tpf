"""
OptionGeneratorAgent - Generates 3 distinct trip options from collaborative research.

Creates budget, balanced, and premium variants with different flight/hotel/experience
combinations, each with booking URLs and trade-off analysis.
"""

import json

from anthropic import Anthropic

from agents.base_agent import BaseAgent
from agents.trip_window import record_nights
from config import effective_temperature, get_api_config
from graph.state import TravelState, TripOption
from metrics.token_tracker import record_temperature, track_usage


# Field names the research agents actually emit. v1.4 read "price" and
# "price_per_night", which no agent writes, so every cost was 0. The old names
# are still accepted as fallbacks for hand-built states in tests.
def _flight_price(f: dict) -> float:
    return float(f.get("price_usd", f.get("price", 0)) or 0)


def _hotel_total(h: dict, nights: int) -> float:
    if h.get("total_usd") is not None:
        return float(h["total_usd"])
    per_night = h.get("price_per_night_usd", h.get("price_per_night", 0)) or 0
    return float(per_night) * nights


def _hotel_per_night(h: dict) -> float:
    return float(h.get("price_per_night_usd", h.get("price_per_night", 0)) or 0)


def _experience_cost(e: dict) -> float:
    return float(e.get("cost_usd", e.get("price", 0)) or 0)

class OptionGeneratorAgent(BaseAgent):
    """Generates 3 diverse trip options from refined research."""

    name = "option_generator"
    description = "Creates budget, balanced, and premium trip variants"

    def _setup(self):
        config = get_api_config()
        self.client = Anthropic(api_key=config.llm.api_key)
        self.model = config.llm.option_generator_model
        self.api_config = config

    async def _execute(self, state: TravelState) -> dict:
        """Generate 3 distinct trip options."""

        intent = state.get("intent", {})
        budget = intent.get("budget_usd", 2000)

        flights = state.get("flights", [])
        hotels = state.get("hotels", [])
        state.get("experiences", [])
        state.get("weather", {})
        state.get("visa_safety", {})

        if not flights or not hotels:
            return self._error_state("Insufficient research data to generate options")

        # Generate 3 options
        option_budget = await self._generate_option(
            state,
            option_id=0,
            style="budget",
            target_spend=budget * 0.75,
            priorities=["cost_effective", "local_experiences", "value"]
        )

        option_balanced = await self._generate_option(
            state,
            option_id=1,
            style="balanced",
            target_spend=budget,
            priorities=["comfort", "variety", "efficiency", "good_value"]
        )

        option_premium = await self._generate_option(
            state,
            option_id=2,
            style="premium",
            target_spend=budget * 1.15,
            priorities=["luxury", "unique_experiences", "convenience", "comfort"]
        )

        return {
            "trip_options": [option_budget, option_balanced, option_premium],
            "status": "options_generated"
        }

    async def _generate_option(
        self,
        state: TravelState,
        option_id: int,
        style: str,
        target_spend: float,
        priorities: list[str]
    ) -> TripOption:
        """Generate a single trip option with specific constraints."""

        flights = state.get("flights", [])
        hotels = state.get("hotels", [])
        experiences = state.get("experiences", [])
        intent = state.get("intent", {})
        weather = state.get("weather", {})

        # Every option is built on the coordinated flight and hotel — the
        # selections the hub's refinement rounds settled on and the final audit
        # checks. Styles differ in experiences, food and extras. v1.4 picked a
        # fresh flight/hotel per style from the full candidate list, so a
        # delivered option could carry a conflict refinement had resolved.
        flight = self._select_flight(flights, style, state.get("selected_flight"))
        hotel = self._select_hotel(hotels, style, state.get("selected_hotel"))
        # The window's nights when there is one, so option costs match the
        # stay the hotel was priced for; otherwise the request's duration.
        num_nights = (
            record_nights(state.get("trip_window")) or intent.get("duration_days") or 7
        )
        selected_experiences = self._select_experiences(
            experiences, style, target_spend, flight, hotel, num_nights
        )

        # Calculate total cost
        flight_cost = _flight_price(flight)
        hotel_total = _hotel_total(hotel, num_nights)
        experience_cost = sum(_experience_cost(e) for e in selected_experiences)
        food_estimate = num_nights * (60 if style == "premium" else 40 if style == "balanced" else 25)
        misc_estimate = 100

        total_cost = flight_cost + hotel_total + experience_cost + food_estimate + misc_estimate

        # Generate day-by-day itinerary
        day_by_day = await self._generate_itinerary(
            state, flight, hotel, selected_experiences, style, num_nights
        )

        # Generate booking URLs
        flight_url = self._generate_flight_booking_url(flight, intent)
        hotel_url = self._generate_hotel_booking_url(hotel, intent)
        experience_urls = [self._generate_experience_booking_url(e, intent) for e in selected_experiences]

        # Generate highlights and trade-offs
        highlights, trade_offs = self._generate_highlights_and_tradeoffs(
            style, flight, hotel, selected_experiences, weather, total_cost, target_spend
        )

        option: TripOption = {
            "option_id": option_id,
            "style": style,
            "title": self._generate_title(style, intent.get("destination", "Unknown")),
            "description": self._generate_description(style, priorities),
            "total_cost_usd": round(total_cost, 2),
            "flight": flight,
            "hotel": hotel,
            "experiences": selected_experiences,
            "itinerary": {
                "num_days": num_nights,
                "destination": intent.get("destination", "Unknown"),
                "style": style,
                "total_cost": round(total_cost, 2),
                "breakdown": {
                    "flight": flight_cost,
                    "hotel": hotel_total,
                    "experiences": experience_cost,
                    "food": food_estimate,
                    "misc": misc_estimate
                }
            },
            "day_by_day": day_by_day,
            "flight_booking_url": flight_url,
            "hotel_booking_url": hotel_url,
            "experience_booking_urls": experience_urls,
            "highlights": highlights,
            "trade_offs": trade_offs
        }

        return option

    def _select_flight(self, flights: list[dict], style: str, selected: dict | None = None) -> dict:
        """The coordinated flight; style-based choice only when there is none."""
        if selected:
            return selected
        if not flights:
            return {}

        if style == "budget":
            return min(flights, key=lambda f: _flight_price(f) or 999999)
        elif style == "premium":
            # Prefer direct flights and higher comfort
            direct_flights = [f for f in flights if f.get("stops", 1) == 0]
            if direct_flights:
                return min(direct_flights, key=lambda f: f.get("duration_hours", 999))
            return min(flights, key=lambda f: f.get("duration_hours", 999))
        else:  # balanced
            # Best value: price/comfort ratio
            return self._best_value_flight(flights)

    def _select_hotel(self, hotels: list[dict], style: str, selected: dict | None = None) -> dict:
        """The coordinated hotel; style-based choice only when there is none."""
        if selected:
            return selected
        if not hotels:
            return {}

        if style == "budget":
            return min(hotels, key=lambda h: _hotel_per_night(h) or 999999)
        elif style == "premium":
            # Highest rated with good location
            return max(hotels, key=lambda h: h.get("rating", 0))
        else:  # balanced
            return self._best_value_hotel(hotels)

    def _select_experiences(
        self,
        experiences: list[dict],
        style: str,
        budget: float,
        flight: dict,
        hotel: dict,
        num_nights: int = 7,
    ) -> list[dict]:
        """Select experiences based on style and remaining budget."""
        if not experiences:
            return []

        # Calculate remaining budget for experiences
        flight_cost = _flight_price(flight)
        hotel_cost = _hotel_total(hotel, num_nights)
        experience_budget = budget - flight_cost - hotel_cost - 400  # Leave room for food/misc

        if style == "budget":
            # Free or cheap experiences
            selected = [e for e in experiences if _experience_cost(e) < 50][:4]
        elif style == "premium":
            # Include premium experiences
            selected = experiences[:6]
        else:  # balanced
            # Mix of paid and free
            selected = experiences[:5]

        # Ensure we don't exceed experience budget
        total = 0
        final_selected = []
        for exp in selected:
            exp_cost = _experience_cost(exp)
            if total + exp_cost <= experience_budget:
                final_selected.append(exp)
                total += exp_cost

        return final_selected

    def _best_value_flight(self, flights: list[dict]) -> dict:
        """Find best value flight (price vs duration)."""
        if not flights:
            return {}

        def value_score(f: dict) -> float:
            price = _flight_price(f) or 999999
            duration = f.get("duration_hours", 24)
            stops = f.get("stops", 2)
            # Lower score is better
            return price + (duration * 20) + (stops * 100)

        return min(flights, key=value_score)

    def _best_value_hotel(self, hotels: list[dict]) -> dict:
        """Find best value hotel (price vs rating)."""
        if not hotels:
            return {}

        def value_score(h: dict) -> float:
            price = _hotel_per_night(h) or 999999
            rating = h.get("rating", 3.0)
            # Higher score is better (invert for min)
            return -(rating * 100 - price)

        return min(hotels, key=value_score)

    @staticmethod
    def _format_arrival_for_prompt(flight: dict) -> str:
        arrival = flight.get("arrival") or ""
        if not arrival:
            return "Unknown arrival time"
        airline = flight.get("airline", "")
        return f"Flight {airline} lands {arrival.replace('T', ' at ')[:19]}".strip()

    @staticmethod
    def _format_weather_for_prompt(weather: dict) -> str:
        if not weather:
            return "Unknown"
        parts = []
        if weather.get("avg_temp_c") is not None:
            parts.append(f"average high {weather['avg_temp_c']}°C")
        if weather.get("precipitation_mm") is not None:
            parts.append(f"{weather['precipitation_mm']} mm rain in the month")
        if weather.get("summary"):
            parts.append(weather["summary"])
        return "; ".join(parts) or "Unknown"

    async def _generate_itinerary(
        self,
        state: TravelState,
        flight: dict,
        hotel: dict,
        experiences: list[dict],
        style: str,
        num_days: int
    ) -> list[dict]:
        """Generate day-by-day itinerary using Claude."""

        intent = state.get("intent", {})
        weather = state.get("weather", {})

        dest = intent.get('destination', 'Unknown')
        prompt = f"""Create a detailed {num_days}-day itinerary for a {style} trip to {dest}.

**Trip Details:**
- Style: {style}
- Budget Level: {"Luxury" if style == "premium" else "Mid-range" if style == "balanced" else "Budget-conscious"}
- Interests: {intent.get('interests', [])}
- Weather: {self._format_weather_for_prompt(weather)}

**Arrival:**
{self._format_arrival_for_prompt(flight)}

**Accommodations:**
{hotel.get('name', 'Unknown Hotel')} in {hotel.get('location', 'unknown area')} - {hotel.get('description', '')}

**Available Experiences:**
{self._format_experiences_for_prompt(experiences)}

Create a day-by-day plan with:
- Day 1 starting only after the arrival time above (nothing scheduled before it)
- Activities reachable from the hotel's area on the same day
- Morning activity
- Afternoon activity
- Evening activity
- Dining suggestions (aligned with {style} budget)

Return ONLY a JSON array of day objects with this structure:
[
  {{
    "day": 1,
    "date": "Day 1",
    "morning": "Activity description",
    "afternoon": "Activity description",
    "evening": "Activity description",
    "meals": {{"breakfast": "...", "lunch": "...", "dinner": "..."}},
    "notes": "Any special notes"
  }},
  ...
]
"""

        option_temperature = effective_temperature(0.7)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            temperature=option_temperature,
            messages=[{"role": "user", "content": prompt}]
        )
        track_usage(response.usage.input_tokens, response.usage.output_tokens, model=self.model)
        record_temperature("option_generator", option_temperature)

        itinerary_text = response.content[0].text

        # Parse JSON response
        try:
            # Extract JSON from markdown code blocks if present
            if "```json" in itinerary_text:
                itinerary_text = itinerary_text.split("```json")[1].split("```")[0]
            elif "```" in itinerary_text:
                itinerary_text = itinerary_text.split("```")[1].split("```")[0]

            day_by_day = json.loads(itinerary_text.strip())
            return day_by_day
        except json.JSONDecodeError:
            # Fallback to simple structure
            return [
                {
                    "day": i + 1,
                    "date": f"Day {i + 1}",
                    "morning": f"Explore {intent.get('destination', 'the area')}",
                    "afternoon": "Activity",
                    "evening": "Dinner and relaxation",
                    "meals": {"breakfast": "Hotel", "lunch": "Local restaurant", "dinner": "Local restaurant"},
                    "notes": ""
                }
                for i in range(num_days)
            ]

    def _format_experiences_for_prompt(self, experiences: list[dict]) -> str:
        """Format experiences for prompt."""
        if not experiences:
            return "General sightseeing and local exploration"

        result = []
        for e in experiences:
            result.append(f"- {e.get('name', 'Unknown')}: {e.get('description', '')[:100]}")
        return "\n".join(result)

    def _generate_flight_booking_url(self, flight: dict, intent: dict) -> str:
        """Generate flight booking URL."""
        config = self.api_config.flight
        origin = intent.get("origin", "JFK")
        destination = intent.get("destination", "Athens")
        date = intent.get("departure_date", "2026-07-01")

        # Use template from config
        url = config.booking_url_template.format(
            origin=origin,
            destination=destination,
            date=date
        )
        return url

    def _generate_hotel_booking_url(self, hotel: dict, intent: dict) -> str:
        """Generate hotel booking URL."""
        config = self.api_config.hotel
        destination = intent.get("destination", "Athens")
        checkin = intent.get("departure_date", "2026-07-01")
        checkout = intent.get("return_date", "2026-07-08")

        url = config.booking_url_template.format(
            destination=destination,
            checkin=checkin,
            checkout=checkout
        )
        return url

    def _generate_experience_booking_url(self, experience: dict, intent: dict) -> str:
        """Generate experience booking URL."""
        config = self.api_config.experience
        destination = intent.get("destination", "Athens")

        url = config.booking_url_template.format(
            destination=destination
        )
        return url

    def _generate_title(self, style: str, destination: str) -> str:
        """Generate option title."""
        if style == "budget":
            return f"Budget Explorer - {destination}"
        elif style == "balanced":
            return f"Classic {destination} Experience"
        else:
            return f"Premium {destination} Escape"

    def _generate_description(self, style: str, priorities: list[str]) -> str:
        """Generate option description."""
        if style == "budget":
            return (
                "Save money without sacrificing the experience."
                " Perfect for travelers who value authentic local experiences over luxury."
            )
        elif style == "balanced":
            return (
                "The sweet spot between comfort and value."
                " Enjoy quality accommodations, diverse experiences, and good food."
            )
        else:
            return (
                "Indulge in the best the destination has to offer."
                " Premium flights, luxury hotels, and exclusive experiences."
            )

    def _generate_highlights_and_tradeoffs(
        self,
        style: str,
        flight: dict,
        hotel: dict,
        experiences: list[dict],
        weather: dict,
        total_cost: float,
        target_spend: float
    ) -> tuple[list[str], list[str]]:
        """Generate highlights and trade-offs for this option."""

        highlights = []
        trade_offs = []

        if style == "budget":
            highlights.append(f"Save ${target_spend - total_cost:.0f} vs. target budget")
            highlights.append(f"{len([e for e in experiences if _experience_cost(e) == 0])} free experiences included")
            highlights.append("Authentic local experiences")

            if flight.get("stops", 0) > 0:
                trade_offs.append(f"{flight.get('stops')} stop(s) on flight")
            if hotel.get("rating", 0) < 4.0:
                trade_offs.append("Basic accommodations (simpler amenities)")
            trade_offs.append("Limited dining at upscale restaurants")

        elif style == "balanced":
            highlights.append("Best value for money")
            highlights.append(f"{hotel.get('rating', 0):.1f}★ rated hotel")
            highlights.append("Mix of paid and free experiences")
            highlights.append("Comfortable travel schedule")

            if total_cost > target_spend:
                trade_offs.append(f"${total_cost - target_spend:.0f} over budget")
            trade_offs.append("Some compromises on luxury")

        else:  # premium
            highlights.append(f"{hotel.get('rating', 0):.1f}★ luxury hotel")
            if flight.get("stops", 0) == 0:
                highlights.append("Direct flight (no layovers)")
            highlights.append("Exclusive and unique experiences")
            highlights.append("Premium dining experiences")

            trade_offs.append(f"${total_cost - target_spend:.0f} over standard budget")
            trade_offs.append("Higher overall investment")

        return highlights, trade_offs
