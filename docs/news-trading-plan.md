# News-to-trade plan

The idea: news breaks → the stock starts moving → an entry alert → an exit alert, with money
protection first, profit second. Planned, not built yet.

## The flow

1. **News arrives** (NSE filing or headline on a watchlist stock). The news-rater (Haiku) rates
   impact and direction. Only high-impact, clearly bullish (or bearish, if shorts are turned on) items go on.
2. **Price must confirm.** Within 30 minutes the stock moves in the news direction on at least
   1.5x its usual volume for that time of day. News without a price reaction is ignored.
3. **Chase guard.** If the stock has already moved more than about 2.5% since the news, skip it:
   most of the move is gone and the risk is a reversal.
4. **Entry alert** with a full plan: stop below the pre-news price (or below VWAP), target 1.5-2x
   the risk, shares sized so the loss at the stop with charges fits the per-trade limit.
5. **Risk gate** (plain code) gives PASS or BLOCK. Then the user says yes or no.
6. **Exit alerts**, in this priority order:
   1. Protect capital: stop hit, or the day's loss limit reached, means out now.
   2. Bad follow-up news on the same stock means out now.
   3. Lock profit: after +1x risk, move the stop to break-even; after +1.5x, trail it.
   4. Time stop: no progress within 30 minutes means out at market.
   5. Target hit, or 15:15 flat-by, means out.

## Before trusting it

- Start logging every rated news item with the price 5, 15, 30 and 60 minutes later. News history is
  not available from free feeds, so the desk has to collect its own.
- After about 4 weeks, replay the rules on that log and the real bars, the same way `desk/replay.py` does now.
- Use it in paper mode only until the replay shows a profit after charges over a meaningful sample.

## Why this order

Most losses come from one bad day, not from missing a winner. So the exits that protect money
always come before the exits that chase profit, and the risk gate is plain code the AI cannot override.
