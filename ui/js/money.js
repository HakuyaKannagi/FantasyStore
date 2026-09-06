export function formatMoney(money) {
  const raw = typeof money?.display === "string" ? money.display.trim() : "";
  if (!raw) throw new Error("金額データを表示できません。");
  return `${raw} 円`;
}
