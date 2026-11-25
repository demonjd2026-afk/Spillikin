with recent_orders as (
    select
        customer_id,
        count(*) as orders_90d
    from {{ ref('fct_orders') }}
    where order_date >= current_date - interval 90 day
    group by customer_id
)

select
    s.customer_id,
    s.plan,
    s.monthly_price,
    date_diff('day', s.started_at, current_date) as tenure_days,
    date_diff('day', c.last_order_date, current_date) as days_since_last_order,
    coalesce(r.orders_90d, 0) as orders_90d,
    c.lifetime_revenue_usd
from {{ ref('stg_subscriptions') }} as s
join {{ ref('dim_customers') }} as c
    on c.customer_id = s.customer_id
left join recent_orders as r
    on r.customer_id = s.customer_id
where s.is_active
