/**
 * Site content endpoints — apps/content/urls.py.
 *
 * Read-only for visitors, plus the contact form. Page bodies, section bodies
 * and FAQ answers are plain text: render them as text, never as HTML.
 */
import { apiGet, apiPost } from "./api-client";
import type {
  ContactMessageInput,
  ContactMessageReceipt,
  FaqItem,
  PageDetail,
  PageListItem,
  SiteBundle,
} from "./types";

/**
 * GET /content/site/
 *
 * Identity, phones, branches and social links in one payload. The header,
 * the footer and every SEO block read from it — fetch once per session.
 */
export async function getSite(): Promise<SiteBundle> {
  return apiGet<SiteBundle>("/content/site/");
}

/** GET /content/pages/ — published pages, for footer nav and the sitemap. */
export async function listPages(): Promise<PageListItem[]> {
  return apiGet<PageListItem[]>("/content/pages/");
}

/** GET /content/pages/{slug}/ — one published page with its active sections. */
export async function getPage(slug: string): Promise<PageDetail> {
  return apiGet<PageDetail>(`/content/pages/${encodeURIComponent(slug)}/`);
}

/** GET /content/faqs/ — active FAQ items in display order. */
export async function listFaqs(params?: { topic?: string }): Promise<FaqItem[]> {
  return apiGet<FaqItem[]>("/content/faqs/", { params });
}

/**
 * POST /content/contact-messages/
 *
 * Anonymous and throttled to 5/min per IP. Returns only a receipt.
 */
export async function sendContactMessage(
  body: ContactMessageInput
): Promise<ContactMessageReceipt> {
  return apiPost<ContactMessageReceipt>("/content/contact-messages/", body);
}
