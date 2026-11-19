// Regra de ligação bip -> CT-e do selo verde do Painel (static/bipados.js).
// Rodar:  node tests/test_bipados.js
const assert = require("assert");
const path = require("path");
require(path.join(__dirname, "..", "static", "bipados.js"));

const T1 = "2026-11-10T15:00:00Z", T2 = "2026-11-12T09:30:00Z", T3 = "2026-11-13T10:00:00Z";
const b = criarBipados([
  ["111", 96945, "5", T1],   // CT-e 096945 série 5
  ["111", 96945, "", T2],    // mesmo número bipado sem série (mais novo)
  ["111", 500, "M", T2],     // minuta
  ["222", 96945, "5", T3],   // outra filial, mesmo número
  ["111", 700, "", T3],      // bipado sem série
]);

// série igual: casa e o primeiro bip vale
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "096945", serie: "5" }), T1);
// zeros à esquerda na série e no número não atrapalham
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "96945", serie: "05" }), T1);
// filial diferente não mistura
assert.strictEqual(b.quando({ cnpj_filial: "222", cte: "096945", serie: "5" }), T3);
assert.strictEqual(b.quando({ cnpj_filial: "333", cte: "096945", serie: "5" }), null);
// minuta: "M" no GW, "99" na chave impressa
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "000500", serie: "M" }), T2);
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "000500", serie: "5" }), null);   // CT-e de mesmo número, outra série
// bip sem série serve a CT-e de qualquer série
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "000700", serie: "5" }), T3);
// CT-e sem série na base casa com qualquer série do bip
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "000500", serie: null }), T2);
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "000500" }), T2);
// número que não é dígito, ou sem bip
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "ABC", serie: "5" }), null);
assert.strictEqual(b.quando({ cnpj_filial: "111", cte: "999999", serie: "5" }), null);
assert.strictEqual(b.quando(null), null);

// selo e data para planilha
assert.ok(b.selo({ cnpj_filial: "111", cte: "096945", serie: "5" }).includes("cte-bip"));
assert.strictEqual(b.selo({ cnpj_filial: "111", cte: "999999", serie: "5" }), "");
assert.match(b.dataIso({ cnpj_filial: "111", cte: "096945", serie: "5" }), /^2026-11-(09|10)$/);  // depende do fuso
assert.strictEqual(b.dataIso({ cnpj_filial: "111", cte: "999999", serie: "5" }), "");
// lista vazia/ausente não quebra
assert.strictEqual(criarBipados(undefined).quando({ cnpj_filial: "1", cte: "1", serie: "5" }), null);

console.log("ok test_bipados.js");
