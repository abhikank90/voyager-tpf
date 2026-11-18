import json

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from metrics.token_tracker import TokenTrackingCallback

from .base_agent import BaseAgent
from .trip_window import record_nights

ITINERARY_PROMPT = """You are a master travel planner. Build a complete, realistic day-by-day itinerary.

Trip details:
- Destination: {destination}
- Duration: {duration_days} days
- Travel dates: {travel_dates}
- Budget breakdown: {budget_breakdown}
- Selected flight: {selected_flight}
- Selected hotel: {selected_hotel}
- Top experiences: {experiences}
- Top beaches: {top_beaches}
- Top restaurants: {top_restaurants}
- Weather: {weather}
- Visa & Safety: {visa_safety}
- Traveller interests: {interests}

Create a detailed JSON itinerary with:
- "summary": 2-3 sentence trip overview
- "highlights": list of 5 trip highlights
- "days": array of day objects, each with: day_number, date, theme,
  morning, afternoon, evening, meals, estimated_cost_usd, tips
- "packing_list": essential items for this specific trip
- "booking_checklist": what to book in advance
- "local_phrases": 5 useful local phrases with pronunciation
- "total_cost_usd": final cost estimate

Be specific, practical, and enthusiastic. Include actual place names and realistic timings."""


class ItineraryBuilderAgent(BaseAgent):
    name = "itinerary_builder"
    description = "Synthesizes all agent outputs into a complete day-by-day itinerary"

    def _setup(self):
        try:
            from config import effective_temperature, get_api_config, get_settings
            model = get_api_config().llm.itinerary_builder_model
        except Exception:
            model = "claude-sonnet-4-6"
        self.llm = ChatAnthropic(
            model=model,
            temperature=effective_temperature(0.4),
            max_tokens=4096,
            max_retries=get_settings().llm_max_retries,
            callbacks=[TokenTrackingCallback(model=model)],
        )
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a master travel planner. Always return valid JSON."),
            ("human", ITINERARY_PROMPT),
        ])

    async def _execute(self, state: dict) -> dict:
        intent = state.get("intent", {})
        budget_breakdown = state.get("budget_breakdown", {})
        window = state.get("trip_window") or {}
        nights = record_nights(window)
        if nights:
            # The dates the flight and hotel were searched for (protocol §5.2).
            duration = nights
            travel_dates = f"{window['departure']} to {window['return']}"
        else:
            # Pre-v1.5 states carry no window. duration_days may be present but
            # None when the request names no length, so `or`, not a .get default.
            duration = intent.get("duration_days") or 14
            travel_year = intent.get("travel_year") or 2026
            travel_dates = f"July 1 - July {1 + duration}, {travel_year}"

        chain = self.prompt | self.llm
        response = await chain.ainvoke({
            "destination": intent.get("destination", ""),
            "duration_days": duration,
            "travel_dates": travel_dates,
            "budget_breakdown": json.dumps(budget_breakdown, indent=2),
            "selected_flight": json.dumps(state.get("selected_flight", {})),
            "selected_hotel": json.dumps(state.get("selected_hotel", {})),
            "experiences": json.dumps(state.get("experiences", []), indent=2),
            "top_beaches": json.dumps(state.get("top_beaches", []), indent=2),
            "top_restaurants": json.dumps(state.get("top_restaurants", []), indent=2),
            "weather": json.dumps(state.get("weather", {})),
            "visa_safety": json.dumps(state.get("visa_safety", {})),
            "interests": ", ".join(intent.get("interests", [])),
        })

        import re
        try:
            text = response.content
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            itinerary = json.loads(json_match.group()) if json_match else {"raw": text}
        except Exception:
            itinerary = {"raw": response.content}

        return {"itinerary": itinerary, "status": "complete"}
