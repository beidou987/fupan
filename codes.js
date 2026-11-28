/* 盘感罗盘 · 兑换码表
 * 校验完全在浏览器内完成；这里只存兑换码的 SHA-256 摘要，不存明文码。
 * 新增/续期：python3 scripts/make_code.py 手机号 到期日 --append
 * 最近发布：2026-11-26（码表变更经 main push 自动发布）
 * 字段：p 手机号 / h 摘要 / e 有效期（含当日）/ n 备注
 */
window.ACCESS_CODES = [
  { p: "19900000000", h: "c01e447df469f326eccaaac2adfc9edb4e8beb8d003b7e8e72189c90e598a4a2", e: "2027-12-31", n: "站长", },
];
