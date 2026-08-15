/**
 * Catalog endpoints — apps/catalog/urls.py, apps/search/urls.py,
 * apps/categories/urls.py, apps/brands/urls.py, apps/attributes/urls.py.
 */
import { apiGet } from "./api-client";
import type {
  Attribute,
  Brand,
  Category,
  CategoryDetail,
  CategoryTreeNode,
  PaginatedResponse,
  ProductDetail,
  ProductListItem,
} from "./types";

export interface ProductListParams {
  category?: string; // category slug
  brand?: string; // brand slug
  min_price?: number | string;
  max_price?: number | string;
  product_type?: "simple" | "variable" | "bundle" | "mix_and_match" | "subscription";
  organic?: boolean;
  origin?: string;
  /** "<attribute_code>:<value_slug>", e.g. "weight:500g" */
  attribute?: string;
  on_sale?: boolean;
  featured?: boolean;
  search?: string;
  ordering?: "price" | "-price" | "created_at" | "-created_at" | "name_en";
  page?: number;
  page_size?: number;
}

/** GET /products/ */
export async function listProducts(
  params?: ProductListParams
): Promise<PaginatedResponse<ProductListItem>> {
  return apiGet<PaginatedResponse<ProductListItem>>("/products/", { params });
}

/** GET /products/{slug}/ — also records a "recently viewed" entry server-side. */
export async function getProduct(slug: string): Promise<ProductDetail> {
  return apiGet<ProductDetail>(`/products/${slug}/`);
}

/** GET /products/{slug}/related/ — plain array, up to 12 items. */
export async function getRelatedProducts(slug: string): Promise<ProductListItem[]> {
  return apiGet<ProductListItem[]>(`/products/${slug}/related/`);
}

/** GET /products/recently-viewed/ — plain array, tied to user or session. */
export async function getRecentlyViewed(): Promise<ProductListItem[]> {
  return apiGet<ProductListItem[]>("/products/recently-viewed/");
}

/** GET /search/?q=... — same filters as listProducts, plus ordering=rank. */
export async function searchProducts(
  q: string,
  params?: Omit<ProductListParams, "search"> & { ordering?: ProductListParams["ordering"] | "rank" }
): Promise<PaginatedResponse<ProductListItem>> {
  return apiGet<PaginatedResponse<ProductListItem>>("/search/", { params: { q, ...params } });
}

/** GET /categories/ — flat, not paginated. Optional ?parent=<id>&search=. */
export async function listCategories(params?: { parent?: number; search?: string }): Promise<Category[]> {
  return apiGet<Category[]>("/categories/", { params });
}

/** GET /categories/tree/ — nested from roots. */
export async function getCategoryTree(): Promise<CategoryTreeNode[]> {
  return apiGet<CategoryTreeNode[]>("/categories/tree/");
}

/** GET /categories/{slug}/ */
export async function getCategory(slug: string): Promise<CategoryDetail> {
  return apiGet<CategoryDetail>(`/categories/${slug}/`);
}

/** GET /brands/ — paginated, optional ?search=. */
export async function listBrands(params?: { search?: string; page?: number }): Promise<PaginatedResponse<Brand>> {
  return apiGet<PaginatedResponse<Brand>>("/brands/", { params });
}

/** GET /brands/{slug}/ */
export async function getBrand(slug: string): Promise<Brand> {
  return apiGet<Brand>(`/brands/${slug}/`);
}

/** GET /attributes/ — plain array, only is_filterable=true attributes. */
export async function listAttributes(): Promise<Attribute[]> {
  return apiGet<Attribute[]>("/attributes/");
}
