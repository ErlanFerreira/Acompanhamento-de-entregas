/* Selo verde "comprovante bipado" ao lado do CT-e no Painel.
 *
 * Recebe a lista de /api/bipados ([filial, número, série, instante do 1º bip]) e
 * diz, para cada CT-e do Painel, quando ele foi bipado na tela de Bipagem.
 * Mesma regra do controle de comprovantes (app/relatorios_bipagem.py):
 * filial + número + série; CT-e sem série na base casa com qualquer série.
 */
(function (raiz) {
  "use strict";

  // Minuta é "M" no GW e "99" na chave impressa; zeros à esquerda não contam.
  function serieNormalizada(s) {
    const v = String(s == null ? "" : s).trim().toUpperCase();
    if (!v) return null;
    return v === "M" || v === "99" ? "M" : (v.replace(/^0+/, "") || "0");
  }

  function criarBipados(bips) {
    const porSerie = new Map();
    const qualquerSerie = new Map();
    (bips || []).forEach(function (b) {  // vêm do mais antigo para o mais novo: o primeiro vale
      const filial = b[0], numero = b[1], serie = b[2] || "", quando = b[3];
      const k1 = filial + "|" + numero + "|" + serie;
      if (!porSerie.has(k1)) porSerie.set(k1, quando);
      const k2 = filial + "|" + numero;
      if (!qualquerSerie.has(k2)) qualquerSerie.set(k2, quando);
    });

    // Instante UTC ("2026-11-12T15:03:00Z") do bip desse CT-e, ou null.
    function quando(r) {
      if (!/^\d+$/.test(String((r && r.cte) || ""))) return null;
      const numero = parseInt(r.cte, 10);
      const serie = serieNormalizada(r.serie);
      if (serie === null) return qualquerSerie.get(r.cnpj_filial + "|" + numero) || null;
      return porSerie.get(r.cnpj_filial + "|" + numero + "|" + serie) ||
        porSerie.get(r.cnpj_filial + "|" + numero + "|") || null;
    }

    function dois(n) { return String(n).padStart(2, "0"); }
    function dataHora(iso) {
      const d = new Date(iso);
      return dois(d.getDate()) + "/" + dois(d.getMonth() + 1) + "/" + d.getFullYear() + " " + dois(d.getHours()) + ":" + dois(d.getMinutes());
    }
    function dataIso(iso) {  // AAAA-MM-DD no horário local (para planilha/CSV)
      const d = new Date(iso);
      return d.getFullYear() + "-" + dois(d.getMonth() + 1) + "-" + dois(d.getDate());
    }

    return {
      quando: quando,
      // HTML do selo (vazio se não foi bipado)
      selo: function (r) {
        const q = quando(r);
        return q ? ' <span class="cte-bip" role="img" aria-label="Comprovante bipado" title="Comprovante bipado em ' + dataHora(q) + '">✓</span>' : "";
      },
      dataIso: function (r) { const q = quando(r); return q ? dataIso(q) : ""; },
      total: porSerie.size
    };
  }

  raiz.criarBipados = criarBipados;
  raiz.serieNormalizadaBip = serieNormalizada;
})(typeof window !== "undefined" ? window : globalThis);
