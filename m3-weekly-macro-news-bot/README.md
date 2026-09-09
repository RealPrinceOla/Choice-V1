# M3 Weekly Macro News Bot

A free-first weekly macroeconomic news intelligence bot for M3 Capital.

## What it does

Every Sunday, the bot:

1. Pulls the weekly economic calendar from the Forex Factory weekly JSON export.
2. Filters the events most relevant to USD, EUR, GBP/USD and gold.
3. Sends the event data to Gemini for analysis.
4. Produces a plain-English weekly macro report covering:
   - What each important event means
   - What the market expects
   - What would represent a stronger or weaker result than expected
   - Potential implications for USD
   - Potential implications for EUR
   - Potential implications for gold
   - Why the reaction could differ from the simple textbook relationship
5. Sends the report to a Telegram channel.

This is a **news intelligence system**, not a buy/sell signal system. It does not generate entries, stop losses, take profits, or trade instructions.

## Data source

Forex Factory currently exposes a weekly JSON export through Fair Economy's calendar data service. The project uses that weekly export rather than scraping the full web page.

## AI

The default model is `gemini-2.5-flash`. The Gemini API currently offers a free tier for this model, subject to Google's rate limits and policies.

## Required GitHub Actions secrets

Add these repository secrets under **Settings → Secrets and variables → Actions**:

- `GEMINI_API_KEY` = Google Gemini API key
- `TELEGRAM_BOT_TOKEN` = Telegram bot token from BotFather
- `TELEGRAM_CHAT_ID` = Telegram channel chat ID or username accepted by the Telegram Bot API

Optional:

- `GEMINI_MODEL` = Gemini model name. Defaults to `gemini-2.5-flash`.

## Schedule

The workflow runs every Sunday at 09:00 Africa/Lagos time. It can also be run manually from the GitHub Actions tab.

## Important design rule

The bot must describe possible macroeconomic reactions, not present them as guaranteed outcomes. A stronger or weaker data release can produce a different market reaction depending on expectations, revisions, interest-rate pricing, yields, positioning, and the broader macro environment.

## Project structure

```text
m3-weekly-macro-news-bot/
├── README.md
├── requirements.txt
├── src/
│   └── main.py
└── .github/
    └── workflows/
        └── weekly-macro.yml
```
