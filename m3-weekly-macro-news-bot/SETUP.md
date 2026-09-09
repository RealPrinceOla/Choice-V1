# M3 Weekly Macro News Bot Setup

The code and GitHub Actions workflow are already in place.

## 1. Create the Telegram bot

Use Telegram's BotFather to create a bot and copy the bot token.

Add the bot as an administrator of the Telegram channel that should receive the weekly report.

Set `TELEGRAM_CHAT_ID` to the channel username, for example `@M3CapitalNews`, if the channel has a public username.

## 2. Create the Gemini API key

Create a Gemini API key in Google AI Studio.

The default model is `gemini-2.5-flash`. The code reads the model from `GEMINI_MODEL`, so it can be changed later without changing the Python code.

## 3. Add GitHub Actions secrets

In the repository:

**Settings → Secrets and variables → Actions → New repository secret**

Create:

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional:

- `GEMINI_MODEL` = `gemini-2.5-flash`

Do not put real credentials into `.env.example` or any committed file.

## 4. Run a manual test

Open:

**Actions → M3 Weekly Macro News → Run workflow**

The workflow also runs automatically every Sunday at 09:00 Africa/Lagos time.

## 5. Expected Telegram output

The weekly report will contain:

- The most important events for the week
- What each event means
- Market expectations
- Stronger-than-expected scenario
- Weaker-than-expected scenario
- Potential USD impact
- Potential EUR impact
- Potential gold impact
- Important exceptions and cross-market considerations
- A short weekly macro summary

The bot intentionally does not provide buy/sell instructions or trade entries.
