/** Money is integer paise everywhere in the API. Format it for humans here. */
export function rupees(paise: number): string {
  if (typeof paise !== "number" || Number.isNaN(paise)) return "-";
  return `\u20b9${(paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Rupees typed by a human -> integer paise, without float drift. */
export function toPaise(rupeeInput: string): number {
  const cleaned = rupeeInput.replace(/[,\s\u20b9]/g, "");
  if (!cleaned) return 0;
  const [whole, frac = ""] = cleaned.split(".");
  const paise = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
  return Number.isFinite(paise) ? paise : 0;
}
