select
    product_id,
    sku,
    category,
    unit_cost
from {{ source('raw', 'products') }}
