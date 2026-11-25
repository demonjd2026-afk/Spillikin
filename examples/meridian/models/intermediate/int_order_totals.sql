with lines as (
    select
        order_id,
        sum(line_revenue) as gross_revenue,
        sum(line_cost) as product_cost,
        count(*) as line_count
    from {{ ref('int_order_lines') }}
    group by order_id
)

select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.currency,
    l.line_count,
    l.gross_revenue,
    l.gross_revenue - o.discount_amount as net_revenue,
    l.product_cost,
    (l.gross_revenue - o.discount_amount) * fx.usd_rate as net_revenue_usd,
    l.product_cost * fx.usd_rate as product_cost_usd,
    (l.gross_revenue - o.discount_amount + o.shipping_amount) * fx.usd_rate as order_total_usd
from {{ ref('stg_orders') }} as o
join lines as l
    on l.order_id = o.order_id
join {{ ref('stg_fx_rates') }} as fx
    on fx.currency = o.currency
    and fx.rate_date = o.order_date
where o.status = 'completed'
