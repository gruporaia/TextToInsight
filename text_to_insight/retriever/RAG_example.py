SCHEMA = {"contexto_schema":"""=== SCHEMA SQLITE (INTROSPECCAO REAL) ===

Tabela: companies_dates
- company_id: INTEGER
- date_joined: TEXT
- year_founded: INTEGER

Tabela: companies_funding
- company_id: INTEGER
- valuation: INTEGER
- funding: INTEGER
- select_investors: TEXT

Tabela: companies_industries
- company_id: INTEGER
- industry: TEXT

Tabela: income_trees
- zipcode: INTEGER
- Estimate_Total: INTEGER
- Margin_of_Error_Total: INTEGER
- Estimate_Median_income: INTEGER
- Margin_of_Error_Median_income: INTEGER
- Estimate_Mean_income: INTEGER
- Margin_of_Error_Mean_income: INTEGER

Tabela: pizza_clean_customer_orders
- order_id: INTEGER
- customer_id: INTEGER
- pizza_id: INTEGER
- exclusions: TEXT
- extras: TEXT
- order_time: TEXT

Tabela: pizza_clean_runner_orders
- order_id: INTEGER
- runner_id: INTEGER
- pickup_time: TEXT
- distance: REAL
- duration: REAL
- cancellation: TEXT

Tabela: pizza_customer_orders
- order_id: INTEGER
- customer_id: INTEGER
- pizza_id: INTEGER
- exclusions: TEXT
- extras: TEXT
- order_time: TEXT

Tabela: pizza_get_exclusions
- row_id: INTEGER
- order_id: INTEGER
- exclusions: INTEGER
- total_exclusions: INTEGER

Tabela: pizza_get_extras
- row_id: INTEGER
- order_id: INTEGER
- extras: INTEGER
- extras_count: INTEGER

Tabela: pizza_names
- pizza_id: INTEGER
- pizza_name: TEXT

Tabela: pizza_recipes
- pizza_id: INTEGER
- toppings: TEXT

Tabela: pizza_runner_orders
- order_id: INTEGER
- runner_id: INTEGER
- pickup_time: TEXT
- distance: TEXT
- duration: TEXT
- cancellation: TEXT

Tabela: pizza_runners
- runner_id: INTEGER
- registration_date: TEXT

Tabela: pizza_toppings
- topping_id: INTEGER
- topping_name: TEXT

Tabela: statistics
- date: TEXT
- state: TEXT
- total_cases: INTEGER
- total_deaths: INTEGER

Tabela: trees
- idx: INTEGER
- tree_id: INTEGER
- tree_dbh: INTEGER
- stump_diam: INTEGER
- status: TEXT
- health: TEXT
- spc_latin: TEXT
- spc_common: TEXT
- address: TEXT
- zipcode: INTEGER
- borocode: INTEGER
- boroname: TEXT
- nta_name: TEXT
- state: TEXT
- latitude: REAL
- longitude: REAL

Tabela: word_list
- words: TEXT
"""}

#"contexto_schema":"""=== SCHEMA SQLITE (INTROSPECCAO REAL) ===

# Tabela: addresses
# - id: INTEGER (PK, NOT NULL)
# - customer_id: INTEGER (NOT NULL)
# - street: TEXT (NOT NULL)
# - city: TEXT (NOT NULL)
# - state: TEXT
# - postal_code: TEXT
# - country: TEXT (DEFAULT='Brazil')
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - customer_id -> customers.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: audit_logs
# - id: INTEGER (PK, NOT NULL)
# - user_id: INTEGER
# - action: TEXT (NOT NULL)
# - entity_name: TEXT
# - entity_id: INTEGER
# - ip_address: TEXT
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - user_id -> users.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: categories
# - id: INTEGER (PK, NOT NULL)
# - parent_id: INTEGER
# - name: TEXT (NOT NULL)
# - slug: TEXT
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - parent_id -> categories.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: coupons
# - id: INTEGER (PK, NOT NULL)
# - code: TEXT (NOT NULL)
# - discount_percent: REAL
# - expires_at: DATETIME
# - active: INTEGER (DEFAULT=1)

# Tabela: customers
# - id: INTEGER (PK, NOT NULL)
# - first_name: TEXT (NOT NULL)
# - last_name: TEXT (NOT NULL)
# - email: TEXT (NOT NULL)
# - phone: TEXT
# - birth_date: DATE
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)

# Tabela: employees
# - id: INTEGER (PK, NOT NULL)
# - department_id: INTEGER
# - first_name: TEXT (NOT NULL)
# - last_name: TEXT (NOT NULL)
# - email: TEXT
# - salary: REAL
# - hired_at: DATE
#   Foreign keys:
#   - department_id -> departments.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: departments
# - id: INTEGER (PK, NOT NULL)
# - name: TEXT (NOT NULL)
# - budget: REAL
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)

# Tabela: inventory
# - id: INTEGER (PK, NOT NULL)
# - product_id: INTEGER (NOT NULL)
# - warehouse_id: INTEGER (NOT NULL)
# - quantity: INTEGER (DEFAULT=0)
# - updated_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - product_id -> products.id (on_update=CASCADE, on_delete=CASCADE)
#   - warehouse_id -> warehouses.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: invoices
# - id: INTEGER (PK, NOT NULL)
# - order_id: INTEGER (NOT NULL)
# - total_amount: REAL (NOT NULL)
# - issued_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
# - paid: INTEGER (DEFAULT=0)
#   Foreign keys:
#   - order_id -> orders.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: order_items
# - id: INTEGER (PK, NOT NULL)
# - order_id: INTEGER (NOT NULL)
# - product_id: INTEGER (NOT NULL)
# - quantity: INTEGER (NOT NULL)
# - unit_price: REAL (NOT NULL)
#   Foreign keys:
#   - order_id -> orders.id (on_update=CASCADE, on_delete=CASCADE)
#   - product_id -> products.id (on_update=CASCADE, on_delete=RESTRICT)

# Tabela: orders
# - id: INTEGER (PK, NOT NULL)
# - customer_id: INTEGER (NOT NULL)
# - order_date: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
# - status: TEXT (DEFAULT='pending')
# - total_amount: REAL
#   Foreign keys:
#   - customer_id -> customers.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: payments
# - id: INTEGER (PK, NOT NULL)
# - order_id: INTEGER (NOT NULL)
# - payment_method_id: INTEGER
# - amount: REAL (NOT NULL)
# - paid_at: DATETIME
# - status: TEXT
#   Foreign keys:
#   - order_id -> orders.id (on_update=CASCADE, on_delete=CASCADE)
#   - payment_method_id -> payment_methods.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: payment_methods
# - id: INTEGER (PK, NOT NULL)
# - provider: TEXT (NOT NULL)
# - method_type: TEXT
# - active: INTEGER (DEFAULT=1)

# Tabela: products
# - id: INTEGER (PK, NOT NULL)
# - category_id: INTEGER
# - supplier_id: INTEGER
# - name: TEXT (NOT NULL)
# - sku: TEXT
# - price: REAL (NOT NULL)
# - stock_quantity: INTEGER (DEFAULT=0)
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - category_id -> categories.id (on_update=CASCADE, on_delete=SET NULL)
#   - supplier_id -> suppliers.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: product_reviews
# - id: INTEGER (PK, NOT NULL)
# - product_id: INTEGER (NOT NULL)
# - customer_id: INTEGER (NOT NULL)
# - rating: INTEGER (NOT NULL)
# - comment: TEXT
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)
#   Foreign keys:
#   - product_id -> products.id (on_update=CASCADE, on_delete=CASCADE)
#   - customer_id -> customers.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: roles
# - id: INTEGER (PK, NOT NULL)
# - name: TEXT (NOT NULL)
# - description: TEXT

# Tabela: shipments
# - id: INTEGER (PK, NOT NULL)
# - order_id: INTEGER (NOT NULL)
# - warehouse_id: INTEGER
# - tracking_code: TEXT
# - shipped_at: DATETIME
# - delivered_at: DATETIME
# - status: TEXT
#   Foreign keys:
#   - order_id -> orders.id (on_update=CASCADE, on_delete=CASCADE)
#   - warehouse_id -> warehouses.id (on_update=CASCADE, on_delete=SET NULL)

# Tabela: suppliers
# - id: INTEGER (PK, NOT NULL)
# - company_name: TEXT (NOT NULL)
# - contact_name: TEXT
# - email: TEXT
# - phone: TEXT
# - country: TEXT

# Tabela: user_roles
# - user_id: INTEGER (PK, NOT NULL)
# - role_id: INTEGER (PK, NOT NULL)
#   Foreign keys:
#   - user_id -> users.id (on_update=CASCADE, on_delete=CASCADE)
#   - role_id -> roles.id (on_update=CASCADE, on_delete=CASCADE)

# Tabela: users
# - id: INTEGER (PK, NOT NULL)
# - username: TEXT (NOT NULL)
# - email: TEXT (NOT NULL)
# - password_hash: TEXT (NOT NULL)
# - active: INTEGER (DEFAULT=1)
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)

# Tabela: warehouses
# - id: INTEGER (PK, NOT NULL)
# - name: TEXT (NOT NULL)
# - city: TEXT
# - capacity: INTEGER
# - created_at: DATETIME (DEFAULT=CURRENT_TIMESTAMP)"""