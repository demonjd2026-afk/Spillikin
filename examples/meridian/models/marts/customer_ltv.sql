with subscription_value as (
    select
        customer_id,
        sum(monthly_price * date_diff('month', started_at, coalesce(cancelled_at, current_date))) as subscription_revenue_usd
    from {{ ref('stg_subscriptions') }}
    group by customer_id
)

select
    c.customer_id,
    c.lifetime_revenue_usd as order_revenue_usd,
    coalesce(s.subscription_revenue_usd, 0) as subscription_revenue_usd,
    c.lifetime_revenue_usd + coalesce(s.subscription_revenue_usd, 0) as ltv_usd,
    case
        when c.lifetime_revenue_usd + coalesce(s.subscription_revenue_usd, 0) >= 1000 then 'gold'
        when c.lifetime_revenue_usd + coalesce(s.subscription_revenue_usd, 0) >= 250 then 'silver'
        else 'bronze'
    end as value_tier
from {{ ref('dim_customers') }} as c
left join subscription_value as s
    on s.customer_id = c.customer_id
