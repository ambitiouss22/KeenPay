/**
 * What each role may actually do, and therefore what it is shown.
 *
 * The lists below mirror `api/core/rbac.py`'s ROLE_PERMISSIONS. That
 * duplication is deliberate and it is one-directional: this file decides what
 * to *render*, never what is *allowed*. The API refuses on its own regardless
 * of what the browser drew, so the worst a mistake here can do is hide a
 * feature from someone entitled to it, or show a button that answers 403.
 * Neither is a security hole; both are avoidable, which is why the mapping is
 * written down in one place instead of being scattered across pages.
 *
 * Two facts from the API that are easy to get wrong from the outside:
 *
 * - Writing to the catalogue needs `admin:policy`, so it is **admin only** —
 *   a manager may read products and never add one.
 * - Building a cart and checking out need `session:create`, which a manager
 *   does **not** hold. Shopping is a buyer's act; a manager approves and
 *   reports on it.
 */

export type Audience = "buyer" | "merchant";

/** Roles that shop, and roles that run the shop. */
const BUYER_ROLES = new Set(["shopper", "agent"]);

/**
 * Which side of the product a role belongs to.
 *
 * This decides the landing screen only. It is derived from the role in the
 * verified token, never from what someone picked on the sign-in page: a
 * chooser that granted merchant screens would be an authorization decision
 * made in a browser, which is no decision at all.
 */
export function audienceFor(role: string | undefined): Audience {
  return role && BUYER_ROLES.has(role) ? "buyer" : "merchant";
}

export interface Feature {
  href: string;
  title: string;
  blurb: string;
  /** Roles the API will actually let through. */
  roles: string[];
  audience: Audience;
}

export const FEATURES: Feature[] = [
  // --- buyer ---------------------------------------------------------------
  {
    href: "/console/chat",
    title: "Chat",
    blurb:
      "Ask an agent for what you want in your own words. It searches the catalogue and recommends; it never sets a price and it cannot pay.",
    roles: ["shopper", "admin", "agent"],
    audience: "buyer",
  },
  {
    href: "/console/shop",
    title: "Shop",
    blurb:
      "Browse the catalogue, build a cart and check out. The catalogue prices the goods; nothing you send can set a price.",
    roles: ["shopper", "admin", "agent"],
    audience: "buyer",
  },
  {
    href: "/console/orders",
    title: "My orders",
    blurb:
      "Look up an order and see exactly what it covers — the lines, the total, and where it has got to.",
    roles: ["shopper", "agent", "support_agent", "manager", "admin"],
    audience: "buyer",
  },
  // --- merchant ------------------------------------------------------------
  {
    href: "/console/products",
    title: "Catalogue",
    blurb: "Add and price the products you sell. Prices are integer paise, and they are the truth.",
    roles: ["admin"],
    audience: "merchant",
  },
  {
    href: "/console/approvals",
    title: "Approvals",
    blurb:
      "The human in the loop. Read what policy decided, what risk scored, and sign off the money movement — or don't.",
    roles: ["manager", "admin"],
    audience: "merchant",
  },
  {
    href: "/console/campaigns",
    title: "Growth",
    blurb:
      "Opportunities and capped campaigns. Budget is reserved atomically and cannot be overspent, however many buyers arrive at once.",
    roles: ["manager", "admin"],
    audience: "merchant",
  },
  {
    href: "/console/audit",
    title: "Audit",
    blurb:
      "The hash-chained trail of every money action, and a signed passport for any payment that verifies without trusting this server.",
    roles: ["support_agent", "manager", "admin"],
    audience: "merchant",
  },
  {
    href: "/console/buy",
    title: "AI buyer",
    blurb:
      "Drive an agent through the protocol gateway and watch its attempt to move money be refused before anything is called.",
    roles: ["admin"],
    audience: "merchant",
  },
];

/** Everything this role may open, in menu order. */
export function featuresFor(role: string | undefined): Feature[] {
  if (!role) return [];
  return FEATURES.filter((f) => f.roles.includes(role));
}

/** How to describe the person, in their own terms. */
export function audienceLabel(audience: Audience): string {
  return audience === "buyer" ? "Buyer" : "Merchant";
}
