select
    upper(currency) as currency,
    rate_date,
    usd_rate
from {{ source('raw', 'fx_rates') }}
