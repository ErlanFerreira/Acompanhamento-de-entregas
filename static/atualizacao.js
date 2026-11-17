/* "Atualizado hoje às 11:10 · há 3 h 20 min" no cabeçalho do painel.
 *
 * Os dados só mudam quando a sincronização roda (GitHub Actions), e o agendador
 * do GitHub atrasa ou pula execuções. Mostrar a idade dos dados deixa claro
 * quando um CT-e "pendente" pode ser só dado velho. Muda de cor:
 * até 5 h normal, de 5 h a 12 h amarelo, mais de 12 h vermelho.
 * O texto se renova sozinho a cada minuto (sem recarregar os dados).
 */
(function () {
  "use strict";
  const AVISO_MIN = 5 * 60;
  const VELHO_MIN = 12 * 60;

  function dois(n) { return String(n).padStart(2, "0"); }

  function quando(t) {
    const hora = dois(t.getHours()) + ":" + dois(t.getMinutes());
    const hoje = new Date();
    const ontem = new Date(); ontem.setDate(hoje.getDate() - 1);
    const mesmoDia = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (mesmoDia(t, hoje)) return "hoje às " + hora;
    if (mesmoDia(t, ontem)) return "ontem às " + hora;
    return dois(t.getDate()) + "/" + dois(t.getMonth() + 1) + " às " + hora;
  }

  function idade(min) {
    if (min < 1) return "agora há pouco";
    if (min < 60) return "há " + min + " min";
    const h = Math.floor(min / 60), m = min % 60;
    if (h < 24) return "há " + h + " h" + (m ? " " + m + " min" : "");
    const d = Math.floor(h / 24);
    return "há " + d + (d === 1 ? " dia" : " dias");
  }

  function pintar(el) {
    const t = new Date(el.dataset.ts);
    if (isNaN(t.getTime())) return;
    const min = Math.max(0, Math.round((Date.now() - t.getTime()) / 60000));
    const nivel = min >= VELHO_MIN ? "velho" : min >= AVISO_MIN ? "aviso" : "ok";
    el.className = "dados-idade " + nivel;
    el.querySelector(".quando").textContent = quando(t);
    el.querySelector(".idade").textContent = idade(min);
    const dica = el.querySelector(".dica");
    if (dica) dica.hidden = nivel === "ok";
  }

  function pintarTodos() {
    document.querySelectorAll(".dados-idade[data-ts]").forEach(pintar);
  }

  // HTML do trecho "Atualizado ...". `textoSemHora` aparece se o servidor não
  // mandou o instante exato. `comDica` acrescenta o aviso do botão "Atualizar agora".
  window.htmlAtualizacao = function (meta, textoSemHora, comDica) {
    if (!meta || !meta.sincronizado_em) return "Atualizado em " + textoSemHora;
    setTimeout(pintarTodos, 0);
    return '<span class="dados-idade ok" data-ts="' + meta.sincronizado_em + '">Atualizado <span class="quando"></span>' +
      ' (<span class="idade"></span>)' +
      (comDica ? '<span class="dica" hidden> — dados podem estar desatualizados, use <strong>↻ Atualizar agora</strong></span>' : "") +
      "</span>";
  };

  setInterval(pintarTodos, 60000);
})();
