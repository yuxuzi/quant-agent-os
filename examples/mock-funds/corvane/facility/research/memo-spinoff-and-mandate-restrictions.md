# Compliance memo: spin-offs and client restrictions

Author: compliance. Date: 2026-03-19.

On 12 March the parent Jorargate Works distributed one share of Jorargate Leaf Corp for every
four parent shares. Jorargate Leaf is a tobacco company. Two separate accounts, Brightwater
Foundation and Cantor Sovereign Fund, forbid tobacco in their mandates, so each held an excluded
security from 12 March to 17 March. It was sold on 18 March. No client was harmed in the sense
of a loss, and both clients were told.

Cause: our restriction check runs on orders, and a spin-off is not an order. Our fix is a daily
check of end-of-day holdings against each account's restrictions. Position caps are checked the
same way. Cantor's 5% single-position cap and Aldergate's 7% cap are in the same check.

The check must not be run by the portfolio managers on their own accounts. It is run by
compliance, who see every account's holdings.
