-- Marketplace Seller Performance Pipeline — core schema.
-- All money columns are DECIMAL per R7. Ranking/filter columns are indexed for the Part 4 API.

SET NAMES utf8mb4;

-- ========================= REFERENCE TABLES =========================

CREATE TABLE IF NOT EXISTS ref_fx_rate (
    rate_date       DATE PRIMARY KEY,
    brl_to_usd      DECIMAL(18,6) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ref_holiday (
    holiday_date    DATE NOT NULL,
    state_code      CHAR(2) NOT NULL DEFAULT '',  -- '' = national holiday (PK cannot contain NULL)
    name            VARCHAR(255) NOT NULL,
    PRIMARY KEY (holiday_date, state_code)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ref_state (
    state_code      CHAR(2) PRIMARY KEY,
    state_name      VARCHAR(100) NOT NULL,
    region          VARCHAR(50) NOT NULL
) ENGINE=InnoDB;

-- Cleaned commission_rates.csv (R12/R13). Reloaded in full every run (tiny config table).
CREATE TABLE IF NOT EXISTS ref_commission_rate (
    rate_id         INT AUTO_INCREMENT PRIMARY KEY,
    tier            ENUM('Bronze','Silver','Gold') NOT NULL,
    rate            DECIMAL(6,4) NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE NULL,             -- NULL = open-ended
    UNIQUE KEY uq_tier_from (tier, effective_from)
) ENGINE=InnoDB;

-- Cleaned category_commission_overrides.csv (R13). Reloaded in full every run.
CREATE TABLE IF NOT EXISTS ref_category_override (
    override_id     INT AUTO_INCREMENT PRIMARY KEY,
    category_name_en VARCHAR(100) NOT NULL,
    fixed_rate      DECIMAL(6,4) NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE NULL,
    notes           VARCHAR(255) NULL,
    UNIQUE KEY uq_cat_from (category_name_en, effective_from)
) ENGINE=InnoDB;

-- ========================= DIMENSIONS =========================

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_unique_id   CHAR(32) PRIMARY KEY,   -- R1
    home_state_code       CHAR(2) NULL,
    home_state_name        VARCHAR(100) NULL,
    home_region             VARCHAR(50) NULL,
    first_order_date        DATE NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_seller (
    seller_id             CHAR(32) PRIMARY KEY,
    seller_zip_prefix      VARCHAR(10) NULL,
    seller_state_code       CHAR(2) NULL,
    seller_state_name        VARCHAR(100) NULL,
    seller_region              VARCHAR(50) NULL,
    latitude                     DECIMAL(9,6) NULL,
    longitude                     DECIMAL(9,6) NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_product (
    product_id             CHAR(32) PRIMARY KEY,
    category_name_pt        VARCHAR(100) NULL,
    category_name_en         VARCHAR(100) NULL,   -- R8: resolved English name, 'unknown' or 'uncategorized'
    category_source           VARCHAR(30) NOT NULL -- patch | kaggle | unknown | uncategorized
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id            VARCHAR(20) PRIMARY KEY,
    name                    VARCHAR(255) NOT NULL,
    start_ts_sp              DATETIME NOT NULL,     -- normalized to America/Sao_Paulo, R18
    end_ts_sp                 DATETIME NOT NULL,
    target_states               JSON NULL,
    target_regions                JSON NULL,
    categories                      JSON NULL,
    priority                          INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dim_date (
    date_key                DATE PRIMARY KEY,
    year_num                  INT NOT NULL,
    month_num                   INT NOT NULL,
    day_num                        INT NOT NULL,
    day_of_week                      TINYINT NOT NULL,  -- 0=Monday .. 6=Sunday
    is_weekend                         TINYINT(1) NOT NULL,
    is_national_holiday                   TINYINT(1) NOT NULL
) ENGINE=InnoDB;

-- ========================= FACTS =========================

CREATE TABLE IF NOT EXISTS fact_order (
    order_id                  CHAR(32) PRIMARY KEY,
    customer_unique_id          CHAR(32) NOT NULL,
    order_status                   VARCHAR(20) NOT NULL,
    customer_state_code               CHAR(2) NULL,          -- order/shipment-level, see R9/R18
    customer_region                      VARCHAR(50) NULL,
    is_revenue_eligible               TINYINT(1) NOT NULL,   -- R2
    has_broken_delivery                  TINYINT(1) NOT NULL,   -- R3
    purchase_ts                             DATETIME NOT NULL,
    approved_at                                DATETIME NULL,
    delivered_carrier_date                        DATETIME NULL,
    delivered_customer_date                          DATETIME NULL,
    estimated_delivery_date                             DATE NULL,
    gross_brl                                              DECIMAL(14,2) NOT NULL DEFAULT 0,
    gross_usd                                                 DECIMAL(14,2) NOT NULL DEFAULT 0,
    net_cash_paid_brl                                            DECIMAL(14,2) NOT NULL DEFAULT 0,  -- R5, excludes vouchers
    payment_total_brl                                               DECIMAL(14,2) NOT NULL DEFAULT 0,
    payment_mismatch                                                   TINYINT(1) NOT NULL DEFAULT 0, -- R5
    fx_rate                                                               DECIMAL(18,6) NULL,
    fx_rate_date                                                             DATE NULL,
    on_time                                                                     TINYINT(1) NULL,        -- R10, NULL if not computable
    delivery_business_days                                                         INT NULL,                -- R9
    review_score                                                                      TINYINT NULL,            -- R14, latest review
    run_id                                                                               BIGINT NOT NULL,
    CONSTRAINT fk_fo_customer FOREIGN KEY (customer_unique_id) REFERENCES dim_customer(customer_unique_id)
) ENGINE=InnoDB;

CREATE INDEX idx_fo_purchase_ts ON fact_order (purchase_ts);
CREATE INDEX idx_fo_customer ON fact_order (customer_unique_id);
CREATE INDEX idx_fo_status ON fact_order (order_status);
CREATE INDEX idx_fo_region ON fact_order (customer_region);

CREATE TABLE IF NOT EXISTS fact_order_item (
    order_id                CHAR(32) NOT NULL,
    order_item_id              INT NOT NULL,
    seller_id                     CHAR(32) NOT NULL,
    product_id                       CHAR(32) NOT NULL,
    price_brl                           DECIMAL(14,2) NOT NULL,
    freight_value_brl                      DECIMAL(14,2) NOT NULL,
    item_gross_brl                            DECIMAL(14,2) NOT NULL,   -- R4: price + freight
    item_gross_usd                               DECIMAL(14,2) NULL,       -- R6
    fx_rate                                         DECIMAL(18,6) NULL,
    fx_rate_date                                       DATE NULL,
    category_name_en                                      VARCHAR(100) NULL,
    seller_tier_at_purchase                                  ENUM('Bronze','Silver','Gold') NOT NULL, -- R12
    commission_rate                                             DECIMAL(6,4) NULL,                       -- R13
    commission_brl                                                 DECIMAL(14,2) NULL,
    commission_usd                                                    DECIMAL(14,2) NULL,
    shipping_limit_date                                                  DATETIME NOT NULL,
    distance_km                                                             DECIMAL(10,3) NULL,              -- R16
    campaign_id                                                                VARCHAR(20) NULL,                 -- R18
    purchase_month                                                               CHAR(7) NOT NULL,                 -- 'YYYY-MM' for R12 joins
    run_id                                                                           BIGINT NOT NULL,
    PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_foi_order FOREIGN KEY (order_id) REFERENCES fact_order(order_id),
    CONSTRAINT fk_foi_seller FOREIGN KEY (seller_id) REFERENCES dim_seller(seller_id),
    CONSTRAINT fk_foi_product FOREIGN KEY (product_id) REFERENCES dim_product(product_id),
    CONSTRAINT fk_foi_campaign FOREIGN KEY (campaign_id) REFERENCES dim_campaign(campaign_id)
) ENGINE=InnoDB;

CREATE INDEX idx_foi_seller_month ON fact_order_item (seller_id, purchase_month);
CREATE INDEX idx_foi_campaign ON fact_order_item (campaign_id);
CREATE INDEX idx_foi_category ON fact_order_item (category_name_en);

-- Seller-within-order lateness (R11) and review attribution (R14).
CREATE TABLE IF NOT EXISTS fact_order_seller (
    order_id                CHAR(32) NOT NULL,
    seller_id                  CHAR(32) NOT NULL,
    earliest_shipping_limit        DATETIME NOT NULL,
    lateness_status                   ENUM('seller_late','carrier_late','on_time') NULL, -- NULL if R3 broken
    review_score                         TINYINT NULL,
    purchase_month                           CHAR(7) NOT NULL,
    run_id                                       BIGINT NOT NULL,
    PRIMARY KEY (order_id, seller_id),
    CONSTRAINT fk_fos_order FOREIGN KEY (order_id) REFERENCES fact_order(order_id),
    CONSTRAINT fk_fos_seller FOREIGN KEY (seller_id) REFERENCES dim_seller(seller_id)
) ENGINE=InnoDB;

CREATE INDEX idx_fos_seller_month ON fact_order_seller (seller_id, purchase_month);

-- ========================= OPERATIONAL / DQ =========================

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    start_date             DATE NOT NULL,
    end_date                  DATE NOT NULL,
    started_at                   DATETIME NOT NULL,
    finished_at                     DATETIME NULL,
    status                             VARCHAR(20) NOT NULL,   -- running | success | failed
    params_json                           JSON NULL,
    row_counts_json                          JSON NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS data_quality_issue (
    issue_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_id                VARCHAR(10) NOT NULL,     -- e.g. 'R3', 'R5', 'R13'
    entity_type               VARCHAR(30) NOT NULL,     -- order | order_item | seller | internal_file_row | ...
    entity_id                    VARCHAR(64) NOT NULL,
    issue_type                      VARCHAR(50) NOT NULL,
    details                             TEXT NULL,
    detected_at                            DATETIME NOT NULL,
    run_id                                    BIGINT NOT NULL,
    CONSTRAINT fk_dq_run FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id)
) ENGINE=InnoDB;

CREATE INDEX idx_dq_rule ON data_quality_issue (rule_id);
CREATE INDEX idx_dq_run ON data_quality_issue (run_id);
