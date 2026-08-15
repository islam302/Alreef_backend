/**
 * TypeScript types for the Alreef e-commerce API (/api/v1/).
 *
 * These are hand-written from the actual Django REST Framework serializers
 * (apps/<app>/serializers.py) — not generated. Keep in sync with:
 *   - apps/core/response.py            (envelope shape)
 *   - apps/core/pagination.py          (paginated shape)
 *   - apps/core/error_codes.py         (ErrorCode union)
 *   - apps/<app>/serializers.py         (per-domain field lists)
 *
 * IMPORTANT — money fields:
 * The backend uses django-money. apps/core/apps.py registers djmoney's DRF
 * MoneyField, which subclasses DRF's DecimalField. DecimalField renders as a
 * STRING in JSON (e.g. "150.00"), not a number. Every monetary amount below
 * is typed `string` for this reason — parse with `parseFloat` / a decimal
 * library on the frontend, never assume it's already a number.
 *
 * This file is duplicated (not imported) into frontend-sdk/types.ts so the
 * SDK package is self-contained. Keep both in sync.
 */

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

/** Every non-paginated API response is wrapped in this envelope. */
export interface ApiResponse<T> {
  success: boolean;
  message: string;
  data: T | null;
  errors: ApiErrorBody | null;
}

/**
 * Shape of `errors` on a non-2xx response. `apps/core/response.py`
 * always builds this as {code, fields} in practice (every call site in the
 * codebase passes `code`).
 *
 * - For DRF ValidationError (400): `fields` is DRF's standard field-error
 *   dict, e.g. { "email": ["This field is required."] }.
 * - For a single business-rule error (ApplicationError): `fields` is usually
 *   `{}` and the human-readable reason is in the top-level `message`.
 */
export interface ApiErrorBody {
  code: ErrorCode;
  fields: Record<string, string[] | string>;
}

/** Shape of `data` for any endpoint using StandardResultsPagination. */
export interface PaginatedResponse<T> {
  results: T[];
  count: number;
  num_pages: number;
  current_page: number;
  page_size: number;
  next: string | null;
  previous: string | null;
}

/** Full list of apps.core.error_codes.ErrorCodes values. */
export type ErrorCode =
  // Generic
  | "VALIDATION_ERROR"
  | "NOT_FOUND"
  | "PERMISSION_DENIED"
  | "THROTTLED"
  | "INTERNAL_ERROR"
  | "METHOD_NOT_ALLOWED"
  | "IDEMPOTENCY_KEY_CONFLICT"
  | "IDEMPOTENCY_KEY_REQUIRED"
  // Auth
  | "AUTH_INVALID_CREDENTIALS"
  | "AUTH_TOKEN_EXPIRED"
  | "AUTH_TOKEN_INVALID"
  | "AUTH_ACCOUNT_DISABLED"
  | "AUTH_EMAIL_NOT_VERIFIED"
  | "AUTH_EMAIL_ALREADY_EXISTS"
  | "AUTH_PHONE_ALREADY_EXISTS"
  | "AUTH_OTP_INVALID"
  | "AUTH_OTP_EXPIRED"
  // Product / catalog
  | "PRODUCT_NOT_FOUND"
  | "PRODUCT_NOT_PUBLISHED"
  | "PRODUCT_OUT_OF_STOCK"
  | "VARIANT_NOT_FOUND"
  // Inventory
  | "INSUFFICIENT_STOCK"
  | "RESERVATION_EXPIRED"
  | "NO_VALID_BATCH"
  // Cart / checkout
  | "CART_EMPTY"
  | "CART_ITEM_NOT_FOUND"
  | "CHECKOUT_SESSION_EXPIRED"
  | "ADDRESS_INVALID"
  | "ADDRESS_NOT_FOUND"
  | "SHIPPING_UNAVAILABLE"
  | "SHIPPING_METHOD_NOT_FOUND"
  // Coupons / promotions
  | "INVALID_COUPON"
  | "COUPON_EXPIRED"
  | "COUPON_USAGE_LIMIT_REACHED"
  | "COUPON_MINIMUM_NOT_MET"
  | "COUPON_NOT_APPLICABLE"
  // Orders
  | "ORDER_NOT_FOUND"
  | "ORDER_CANNOT_CANCEL"
  | "ORDER_INVALID_TRANSITION"
  // Payments
  | "PAYMENT_FAILED"
  | "PAYMENT_EXPIRED"
  | "PAYMENT_AMOUNT_MISMATCH"
  | "PAYMENT_PROVIDER_UNAVAILABLE"
  | "PAYMENT_ALREADY_CAPTURED"
  | "PAYMENT_WEBHOOK_INVALID_SIGNATURE"
  | "REFUND_EXCEEDS_CAPTURED_AMOUNT"
  // Gift cards / loyalty
  | "GIFT_CARD_INVALID"
  | "GIFT_CARD_EXPIRED"
  | "GIFT_CARD_INSUFFICIENT_BALANCE"
  | "LOYALTY_INSUFFICIENT_POINTS"
  // Reviews
  | "REVIEW_NOT_ELIGIBLE"
  | "REVIEW_ALREADY_EXISTS";

// ---------------------------------------------------------------------------
// Users / auth (apps/users/serializers.py, models.py)
// ---------------------------------------------------------------------------

export type Gender = "male" | "female" | "unspecified";
export type Language = "ar" | "en";

export interface User {
  id: number;
  email: string;
  phone: string | null;
  first_name: string;
  last_name: string;
  full_name: string;
  profile_image: string | null;
  email_verified: boolean;
  phone_verified: boolean;
  date_joined: string; // ISO 8601
  last_login: string | null;
}

export interface CustomerProfile {
  date_of_birth: string | null; // YYYY-MM-DD
  gender: Gender | "";
  marketing_consent: boolean;
  language: Language;
  currency: string; // e.g. "EGP"
}

/** GET/PATCH /api/v1/auth/me/ — User + nested profile. */
export interface Me extends User {
  profile?: CustomerProfile;
}

export type AddressLabel = "home" | "work" | "other";

export interface Address {
  id: number;
  label: AddressLabel;
  full_name: string;
  phone: string;
  governorate: number; // Governorate id (write: id; read: also id)
  governorate_name: string;
  city: number | null;
  city_name: string | null;
  district: string;
  street: string;
  building: string;
  floor: string;
  apartment: string;
  landmark: string;
  postal_code: string;
  notes: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

/** Body for POST/PUT /api/v1/addresses/ (id/timestamps are read-only). */
export type AddressInput = Omit<
  Address,
  "id" | "governorate_name" | "city_name" | "created_at" | "updated_at"
>;

/** Login response data (rest_framework_simplejwt TokenObtainPairSerializer + user). */
export interface LoginResult {
  access: string;
  refresh: string;
  user: User;
}

export interface TokenRefreshResult {
  access: string;
  /** Present because ROTATE_REFRESH_TOKENS=True — always store the new refresh token. */
  refresh: string;
}

// ---------------------------------------------------------------------------
// Categories / Brands / Attributes
// ---------------------------------------------------------------------------

export interface Category {
  id: number;
  name_en: string;
  name_ar: string;
  slug: string;
  image: string | null;
  icon: string;
  parent: number | null;
}

export interface CategoryTreeNode {
  id: number;
  name_en: string;
  name_ar: string;
  slug: string;
  image: string | null;
  icon: string;
  children: CategoryTreeNode[];
}

export interface CategoryBreadcrumb {
  id: number;
  name_en: string;
  slug: string;
}

export interface CategoryDetail {
  id: number;
  name_en: string;
  name_ar: string;
  slug: string;
  description: string;
  parent: number | null;
  image: string | null;
  icon: string;
  seo_title: string;
  seo_description: string;
  breadcrumbs: CategoryBreadcrumb[];
}

export interface Brand {
  id: number;
  name: string;
  slug: string;
  description: string;
  logo: string | null;
  website: string;
  country_of_origin: string;
}

export type AttributeInputType = "select" | "multiselect" | "text" | "number" | "boolean";

export interface AttributeValue {
  id: number;
  value_en: string;
  value_ar: string;
  slug: string;
}

export interface Attribute {
  id: number;
  name_en: string;
  name_ar: string;
  code: string;
  input_type: AttributeInputType;
  is_variant_attribute: boolean;
  is_filterable: boolean;
  unit: string;
  values: AttributeValue[];
}

// ---------------------------------------------------------------------------
// Catalog (apps/catalog/serializers.py, models.py)
// ---------------------------------------------------------------------------

export type ProductType = "simple" | "variable" | "bundle" | "mix_and_match" | "subscription";
export type PackagingType = "jar" | "bottle" | "box" | "bag" | "can" | "pouch" | "gift_box";
export type TaxCategory = "standard" | "zero_rated" | "exempt";

/** Stored badges (ProductBadgeCode). OUT_OF_STOCK/LOW_STOCK/SALE are computed, never stored. */
export type StoredBadge = "NEW" | "BEST_SELLER" | "SALE" | "LIMITED" | "ORGANIC" | "FEATURED";
export type ComputedBadge = StoredBadge | "OUT_OF_STOCK" | "LOW_STOCK";

export interface ProductImage {
  id: number;
  image: string; // absolute URL
  alt_text: string;
  is_primary: boolean;
  sort_order: number;
  variant: number | null;
}

export interface ProductVideo {
  id: number;
  video_url: string;
  thumbnail: string | null;
  provider: "youtube" | "vimeo" | "self_hosted";
  duration_seconds: number | null;
}

export interface VariantStock {
  available_quantity: number;
  is_low_stock: boolean;
  is_out_of_stock: boolean;
}

export interface ProductVariant {
  id: number;
  sku: string;
  barcode: string;
  name_suffix: string; // e.g. "500g"
  price: string; // decimal string, e.g. "450.00"
  compare_at_price: string | null;
  weight_grams: number | null;
  is_default: boolean;
  attributes: AttributeValue[];
  images: ProductImage[];
  stock: VariantStock;
}

export interface BundleItem {
  id: number;
  component: ProductVariant;
  quantity: number;
}

/** Free-form JSON — keys observed in seed data / admin, not schema-enforced. */
export interface HoneyMetadata {
  honey_type?: string;
  floral_source?: string;
  origin?: string;
  raw_honey?: boolean;
  organic?: boolean;
  filtered?: boolean;
  [key: string]: unknown;
}

export interface FoodMetadata {
  ingredients?: string;
  allergens?: string[];
  storage_instructions?: string;
  shelf_life?: string;
  country_of_origin?: string;
  nutritional_information?: Record<string, number>;
  [key: string]: unknown;
}

/** GET /api/v1/products/ item shape (ProductListSerializer). */
export interface ProductListItem {
  id: number;
  name_en: string;
  name_ar: string;
  slug: string;
  short_description: string;
  brand: Brand | null;
  category: Category;
  price: string;
  compare_at_price: string | null;
  product_type: ProductType;
  badges: StoredBadge[];
  computed_badges: ComputedBadge[];
  primary_image: ProductImage | null;
  in_stock: boolean;
  featured: boolean;
}

/** GET /api/v1/products/{slug}/ shape (ProductDetailSerializer). */
export interface ProductDetail {
  id: number;
  name_en: string;
  name_ar: string;
  slug: string;
  sku: string;
  barcode: string;
  description: string;
  short_description: string;
  brand: Brand | null;
  category: Category;
  product_type: ProductType;
  price: string;
  compare_at_price: string | null;
  tax_category: TaxCategory;
  weight_grams: number | null;
  packaging_type: PackagingType | "";
  badges: StoredBadge[];
  computed_badges: ComputedBadge[];
  food_metadata: FoodMetadata;
  honey_metadata: HoneyMetadata;
  images: ProductImage[];
  videos: ProductVideo[];
  variants: ProductVariant[];
  attributes: AttributeValue[];
  bundle_items: BundleItem[];
  seo_title: string;
  seo_description: string;
}

// ---------------------------------------------------------------------------
// Cart (apps/carts/serializers.py, models.py)
// ---------------------------------------------------------------------------

export interface CartItem {
  id: number;
  variant: ProductVariant;
  product_name: string;
  quantity: number;
  unit_price_snapshot: string;
  line_total: string;
}

export interface Cart {
  /** UUID — cart uses a UUID primary key. */
  id: string;
  currency: string;
  coupon_code: string;
  items: CartItem[];
  subtotal: string;
  item_count: number;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Coupons / Promotions
// ---------------------------------------------------------------------------

export type CouponDiscountType = "percentage" | "fixed_amount" | "free_shipping";

/** GET /api/v1/coupons/{code}/ (preview only — cart applies via /cart/coupon/). */
export interface Coupon {
  code: string;
  description: string;
  discount_type: CouponDiscountType;
  percentage_value: string | null;
  fixed_amount_value: string | null;
  minimum_order_amount: string | null;
  maximum_discount_amount: string | null;
  expiration_date: string | null;
}

export type PromotionType =
  | "percentage"
  | "fixed_amount"
  | "free_shipping"
  | "buy_x_get_y"
  | "bundle_discount";

/** GET /api/v1/promotions/ — automatic promotions, no code needed. */
export interface Promotion {
  id: number;
  name: string;
  description: string;
  promotion_type: PromotionType;
  percentage_value: string | null;
  fixed_amount_value: string | null;
  buy_quantity: number | null;
  get_quantity: number | null;
  get_discount_percent: string;
  start_date: string | null;
  end_date: string | null;
}

// ---------------------------------------------------------------------------
// Shipping (apps/shipping/serializers.py, models.py)
// ---------------------------------------------------------------------------

export interface Governorate {
  id: number;
  code: string;
  name_en: string;
  name_ar: string;
}

export interface City {
  id: number;
  name_en: string;
  name_ar: string;
  governorate: number;
}

export type ShippingMethodType = "standard" | "express" | "same_day" | "pickup";

export interface ShippingMethod {
  id: number;
  name: string;
  method_type: ShippingMethodType;
  description: string;
  min_delivery_days: number;
  max_delivery_days: number;
}

/** GET /api/v1/shipping/quotes/ item — one per available ShippingMethod. */
export interface ShippingQuote {
  method_id: number;
  method_name: string;
  method_type: ShippingMethodType;
  min_delivery_days: number;
  max_delivery_days: number;
  price: string;
  currency: string;
  is_free: boolean;
}

export type ShipmentStatus =
  | "pending"
  | "picked_up"
  | "in_transit"
  | "out_for_delivery"
  | "delivered"
  | "failed"
  | "returned"
  | "cancelled";

export interface ShipmentEvent {
  status: ShipmentStatus;
  description: string;
  location: string;
  occurred_at: string;
}

export interface ShipmentTracking {
  tracking_number: string;
  carrier_code: string;
  status: ShipmentStatus;
  estimated_delivery: string | null; // YYYY-MM-DD
  delivered_at: string | null;
  events: ShipmentEvent[];
}

// ---------------------------------------------------------------------------
// Checkout (apps/checkout/serializers.py)
// ---------------------------------------------------------------------------

export type PaymentMethod =
  | "card"
  | "fawry_pay"
  | "mobile_wallet"
  | "valu"
  | "sympl"
  | "aman"
  | "cod";

/** Body for POST /api/v1/checkout/summary/ and POST /api/v1/checkout/. */
export interface CheckoutInput {
  address_id: number;
  shipping_method_id: number;
  payment_method: PaymentMethod;
  coupon_code?: string;
  notes?: string;
}

/** Response data for POST /api/v1/checkout/summary/. */
export interface CheckoutTotals {
  subtotal: string;
  coupon_discount: string;
  promotion_discount: string;
  tax_total: string;
  shipping_total: string;
  cod_fee: string;
  grand_total: string;
  currency: string;
  free_shipping: boolean;
}

/** Response data for POST /api/v1/checkout/ (order creation). */
export interface CheckoutResult {
  order: OrderDetail;
  payment: {
    id: number;
    provider: string;
    status: PaymentStatus;
    redirect_url: string;
    iframe_url: string;
    provider_reference: string;
  };
}

// ---------------------------------------------------------------------------
// Orders (apps/orders/serializers.py, models.py)
// ---------------------------------------------------------------------------

export type OrderStatus =
  | "pending"
  | "confirmed"
  | "processing"
  | "packed"
  | "shipped"
  | "out_for_delivery"
  | "delivered"
  | "cancelled"
  | "return_requested"
  | "returned"
  | "refunded"
  | "partially_refunded";

export interface OrderItem {
  id: number;
  product: number | null;
  variant: number | null;
  product_name_snapshot: string;
  variant_name_snapshot: string;
  sku_snapshot: string;
  unit_price: string;
  quantity: number;
  discount_amount: string;
  tax_amount: string;
  line_total: string;
}

export interface OrderStatusHistoryEntry {
  from_status: string;
  to_status: string;
  note: string;
  created_at: string;
}

/** GET /api/v1/orders/ item shape (OrderListSerializer). */
export interface OrderListItem {
  id: number;
  order_number: string;
  status: OrderStatus;
  grand_total: string;
  currency: string;
  item_count: number;
  created_at: string;
}

/** Snapshot stored on the Order at checkout time — see create_order_from_cart. */
export interface ShippingAddressSnapshot {
  label: string;
  full_name: string;
  phone: string;
  governorate: string;
  city: string;
  district: string;
  street: string;
  building: string;
  floor: string;
  apartment: string;
  landmark: string;
  postal_code: string;
  notes: string;
}

/** GET /api/v1/orders/{order_number}/ shape (OrderDetailSerializer). */
export interface OrderDetail {
  id: number;
  order_number: string;
  status: OrderStatus;
  currency: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string;
  shipping_address_snapshot: ShippingAddressSnapshot;
  shipping_method_name: string;
  payment_method: PaymentMethod;
  subtotal: string;
  coupon_code: string;
  coupon_discount: string;
  promotion_discount: string;
  loyalty_discount: string;
  gift_card_amount_used: string;
  store_credit_amount_used: string;
  tax_total: string;
  shipping_total: string;
  cod_fee: string;
  grand_total: string;
  notes: string;
  cancel_reason: string;
  items: OrderItem[];
  status_history: OrderStatusHistoryEntry[];
  confirmed_at: string | null;
  shipped_at: string | null;
  delivered_at: string | null;
  cancelled_at: string | null;
  created_at: string;
}

export type ReturnReason =
  | "damaged"
  | "wrong_product"
  | "missing_product"
  | "expired_product"
  | "quality_issue"
  | "customer_change_of_mind";

export type ReturnRequestStatus =
  | "requested"
  | "approved"
  | "rejected"
  | "item_received"
  | "refunded"
  | "closed";

export interface ReturnItem {
  order_item: number;
  quantity: number;
}

export interface ReturnRequest {
  id: number;
  order: number;
  reason: ReturnReason;
  description: string;
  status: ReturnRequestStatus;
  resolution_note: string;
  items: ReturnItem[];
  created_at: string;
}

/** Body for POST /api/v1/orders/{order_number}/returns/. */
export interface ReturnRequestInput {
  reason: ReturnReason;
  description?: string;
  items: Array<{ order_item_id: number; quantity: number }>;
}

// ---------------------------------------------------------------------------
// Payments (apps/payments/serializers.py, models.py)
// ---------------------------------------------------------------------------

export type PaymentProvider = "paymob" | "fawry" | "valu" | "cod" | "manual";

export type PaymentStatus =
  | "pending"
  | "authorized"
  | "captured"
  | "failed"
  | "cancelled"
  | "expired"
  | "refunded"
  | "partially_refunded";

export interface Payment {
  id: number;
  order_number: string;
  provider: PaymentProvider;
  method: PaymentMethod;
  amount: string;
  status: PaymentStatus;
  provider_reference: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export type RefundStatus = "pending" | "processed" | "failed";
export type RefundDestination = "original_method" | "store_credit";

/** Admin-only in the current backend (RefundCreateView requires IsAdminUser). */
export interface Refund {
  id: number;
  payment: number;
  order: number;
  amount: string;
  reason: string;
  destination: RefundDestination;
  status: RefundStatus;
  provider_refund_id: string;
  processed_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Reviews (apps/reviews/serializers.py)
// ---------------------------------------------------------------------------

export interface ReviewImage {
  id: number;
  image: string;
}

export interface Review {
  id: number;
  user_name: string;
  rating: number; // 1-5
  title: string;
  comment: string;
  images: ReviewImage[];
  verified_purchase: boolean;
  helpful_votes: number;
  created_at: string;
}

/** Body for POST /api/v1/reviews/. */
export interface ReviewInput {
  product_id: number;
  rating: number;
  title?: string;
  comment?: string;
}

// ---------------------------------------------------------------------------
// Wishlist
// ---------------------------------------------------------------------------

export interface WishlistItem {
  id: number;
  product: ProductListItem;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Loyalty (apps/loyalty/serializers.py)
// ---------------------------------------------------------------------------

export interface LoyaltyAccount {
  points_balance: number;
  lifetime_points_earned: number;
  tier_name: string | null;
}

export type LoyaltyTransactionType = "earn" | "redeem" | "expire" | "adjust";

export interface LoyaltyTransaction {
  transaction_type: LoyaltyTransactionType;
  points: number; // positive for earn, negative for redeem/expire
  order: number | null;
  note: string;
  created_at: string;
  expires_at: string | null;
}

/** Response for POST /api/v1/loyalty/redeem/ — a preview/redeem discount value. */
export interface LoyaltyRedeemResult {
  discount_value: string;
  currency: string;
}

// ---------------------------------------------------------------------------
// Gift cards / store credit (apps/gift_cards/serializers.py)
// ---------------------------------------------------------------------------

export type GiftCardStatus = "active" | "redeemed" | "expired" | "disabled";

/** GET result of POST /api/v1/gift-cards/check/ — code is always masked. */
export interface GiftCard {
  masked_code: string; // e.g. "****-****-AB12"
  balance: string;
  status: GiftCardStatus;
  expires_at: string | null;
}

export interface StoreCreditAccount {
  balance: string;
}

export type StoreCreditReason = "refund" | "promotional" | "manual_admin" | "redemption";

export interface StoreCreditTransaction {
  amount: string; // negative when spent
  reason: StoreCreditReason;
  order: number | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Subscriptions (apps/subscriptions/serializers.py)
// ---------------------------------------------------------------------------

export type SubscriptionFrequency = "weekly" | "monthly" | "quarterly" | "custom";
export type SubscriptionStatus = "active" | "paused" | "cancelled";
export type SubscriptionScheduleStatus = "pending" | "processed" | "skipped" | "failed";

export interface SubscriptionItem {
  id: number;
  variant: number;
  quantity: number;
}

export interface SubscriptionScheduleEntry {
  scheduled_date: string;
  status: SubscriptionScheduleStatus;
  order: number | null;
  failure_reason: string;
}

export interface Subscription {
  id: number;
  name: string;
  address: number;
  shipping_method: number;
  payment_method: PaymentMethod;
  frequency: SubscriptionFrequency;
  custom_interval_days: number | null;
  status: SubscriptionStatus;
  next_order_date: string;
  last_order_date: string | null;
  items: SubscriptionItem[];
  schedule_entries: SubscriptionScheduleEntry[];
  created_at: string;
}

/** Body for POST /api/v1/subscriptions/create/. */
export interface SubscriptionInput {
  name: string;
  address_id: number;
  shipping_method_id: number;
  payment_method: PaymentMethod;
  frequency: SubscriptionFrequency;
  custom_interval_days?: number | null;
  start_date: string; // YYYY-MM-DD
  items: Array<{ variant_id: number; quantity?: number }>;
}

// ---------------------------------------------------------------------------
// Notifications (apps/notifications/serializers.py)
// ---------------------------------------------------------------------------

export type NotificationEvent =
  | "registration"
  | "email_verification"
  | "phone_verification"
  | "password_reset"
  | "order_created"
  | "payment_success"
  | "payment_failed"
  | "order_shipped"
  | "out_for_delivery"
  | "order_delivered"
  | "refund_processed"
  | "low_stock"
  | "abandoned_cart"
  | "promotion"
  | "expiration_alert";

export interface Notification {
  id: number;
  event: NotificationEvent;
  title: string;
  body: string;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface NotificationPreference {
  marketing_email: boolean;
  marketing_sms: boolean;
  marketing_whatsapp: boolean;
  marketing_push: boolean;
}
