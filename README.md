# LevelUp Swing Trader

A free paper-trading swing scanner and dashboard.

This project does not use real money. It tracks real market data, scores swing-trade setups, makes simulated buys/sells, and publishes a dashboard.

## What it does

(1) Pulls daily stock data from Alpha Vantage.

(2) Scores each ticker using a swing-trading model:
Momentum
Trend
Relative strength
Volume confirmation
RSI health
Volatility control
Learning weight based on past closed trades

(3) Simulates fake-money trades:
Buys the highest-ranked stocks when they pass the score threshold
Sells when they hit stop loss, take profit, trend breakdown, or weak score

(4) Generates:
docs/index.html dashboard
data/signals.csv
data/equity_curve.csv
data/latest_run.json

(5) Runs automatically with GitHub Actions.

## Free setup

You need a free Alpha Vantage API key:
https://www.alphavantage.co/support/#api-key

In GitHub:
Settings -> Secrets and variables -> Actions -> New repository secret

Name:
ALPHA_VANTAGE_API_KEY

Value:
your API key

## GitHub Pages

Settings -> Pages

Source:
Deploy from branch

Branch:
main

Folder:
/docs

## Run schedule

The included GitHub Action runs every weekday after market close.

You can change the cron schedule in:
.github/workflows/swing-trader.yml

## Edit the watchlist

Open:
portfolio.json

Change the `symbols` list.

## Important

This is educational paper trading only. It is not financial advice. It does not guarantee profit. It is a disciplined simulation tool.
