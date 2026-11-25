select
    customer_id,
    lower(email) as email,
    full_name,
    upper(country_code) as country_code,
    created_at as signed_up_at,
    marketing_opt_in
from {{ source('raw', 'customers') }}
