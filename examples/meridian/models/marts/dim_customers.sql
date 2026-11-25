with orders as (
    select
        customer_id,
        count(*) as order_count,
        sum(net_revenue_usd) as lifetime_revenue_usd,
        min(order_date) as first_order_date,
        max(order_date) as last_order_date
    from {{ ref('fct_orders') }}
    group by customer_id
)

select
    c.customer_id,
    c.email,
    c.country_code,
    c.signed_up_at,
    c.marketing_opt_in,
    coalesce(o.order_count, 0) as order_count,
    coalesce(o.lifetime_revenue_usd, 0) as lifetime_revenue_usd,
    o.first_order_date,
    o.last_order_date,
    rank() over (partition by c.country_code order by o.lifetime_revenue_usd desc) as country_revenue_rank
from {{ ref('stg_customers') }} as c
left join orders as o
    on o.customer_id = c.customer_id
