/**
 * Product artwork.
 *
 * A catalogue row carries its image in the free-form `attributes` bag rather
 * than a dedicated column, so a merchant can add or change artwork without a
 * migration. That flexibility is also why nothing here trusts the value: the
 * bag is `dict[str, Any]` on the API side, so a row may carry no image, a null,
 * or something that is not a string at all.
 *
 * `productImage` therefore always returns a usable path. A card with a missing
 * picture should look like a product without a photo yet — not like a broken
 * page.
 */

/** Shown when a product has no artwork of its own. */
export const PRODUCT_IMAGE_FALLBACK = "/products/placeholder.svg";

/** The image path for a product, falling back to the neutral tile. */
export function productImage(attributes?: Record<string, unknown> | null): string {
  const raw = attributes?.image_url;
  return typeof raw === "string" && raw.trim() !== "" ? raw : PRODUCT_IMAGE_FALLBACK;
}
