select
    order_date,
    count(*) as order_count,
    sum(net_revenue_usd) as net_revenue_usd,
    sum(margin_usd) as margin_usd,
    sum(case when is_fully_paid then net_revenue_usd else 0 end) as paid_revenue_usd
from {{ ref('fct_orders') }}
group by order_date
