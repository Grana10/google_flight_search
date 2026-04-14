✈️ Flight price monitoring script

Python script that scrapes and analyzes flight prices from Google Flights to optimize a real travel decision (Madrid → California roadtrip).

It monitors hundreds of flight combinations automatically and transforms noisy price fluctuations into structured, decision-ready insights.

🧠 Motivation

Planning a trip between Madrid and California revealed a clear problem:

Prices change constantly
There are too many route, date and duration combinations to evaluate manually
Decision-making becomes slow and based on intuition rather than data

This script was built to turn that process into a structured data problem.

⚙️ What it does

The script automatically:

Generates flight combinations (open-jaw and round-trip routes)
Builds Google Flights queries using encoded tfs= filters
Scrapes flight results directly from Google Flights
Extracts structured information:
Total price (for all passengers)
Flight duration
Number of stops
Airline
Classifies results into:
Direct flights
1-stop flights
Runs continuous monitoring cycles
🔬 Key insight layer

Beyond scraping, the script performs analysis:

Evaluates hundreds of combinations per cycle
Identifies cheaper configurations across the full search space
Highlights trade-offs between cost and convenience
Produces ranked summaries of optimal options
🧩 Architecture highlights
1. Multi-route search system

Evaluates 4 travel configurations:

MAD → LAX + SFO → MAD (open-jaw)
MAD → SFO + LAX → MAD (inverse open-jaw)
MAD → LAX + LAX → MAD (round-trip LA)
MAD → SFO + SFO → MAD (round-trip SF)
2. Two-phase optimization strategy

To reduce unnecessary scraping:

Phase 1 — exploration

Tests a central duration (e.g. 15 days)
Identifies promising departure dates

Phase 2 — refinement

Expands only promising cases
Tests full duration range (13–17 days)

➡️ Reduces requests by ~85% while preserving coverage

3. Google Flights parameter engineering
Uses encoded tfs= protobuf filters
Enables precise control of:
Departure and return dates
Trip structure (round-trip vs multi-city)
Number of passengers
4. Scraping layer
Selenium-based browser automation
Dynamic waits (no fixed delays)
Handles Google consent screens
Parses flight data via structured aria-label fields
📊 Output

The script generates:

vuelos_log.json

Structured dataset containing:

Prices
Routes
Durations
Stops
Timestamps
Alert flags
summary_*.txt

Human-readable report with:

Best flights per category
Cheapest configurations
Key insights per cycle
🚨 Alert system

Triggers alerts when:

Price drops below configured threshold
Better configurations are detected
New optimal combinations appear

Supports:

Desktop notifications (Windows/macOS)
Sound alerts
Optional browser auto-open
🧪 Tech stack
Python 3
Selenium
BeautifulSoup4
webdriver-manager
fast-flights
plyer
⚠️ Notes
Google Flights HTML structure may change at any time
Scraping is slower than API-based solutions but has zero marginal cost
Designed for personal research and experimentation
Requires Chrome installed locally
🧠 Key learnings
Data validation is critical (not optional)
Pricing assumptions (e.g. per passenger vs total) can break analysis
Reducing search space is as important as scraping itself
Encoded query parameters can hide essential control over data retrieval
🚀 Possible extensions
Store results in a database (SQLite / PostgreSQL)
Price evolution tracking over time
Email / Telegram alerts
Streamlit dashboard for visualization
Parallelized scraping engine