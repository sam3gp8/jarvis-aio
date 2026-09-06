"""Localized templates for JARVIS's deterministic safety/security notifications.

Freeze, intrusion and lockdown notifications are generated WITHOUT the LLM so
they stay reliable — which means they need their own translations to honour a
non-English household (the agent's language directive only covers LLM-generated
replies). English is the base and the fallback for any language or key not
present here. Entity/area names and the user's honorific are passed through as
parameters and never translated.

Scope: the self-contained safety messages. The composed lockdown variants that
stitch together device lists ("I locked X and closed Y, but Z is open") are not
here yet — localizing generated, grammar-sensitive device phrases is a separate
pass, and a half-translated sentence reads worse than a clean English one.
"""
from __future__ import annotations

_FALLBACK = "en"


def _norm(lang: str | None) -> str:
    return (lang or "en").split("-")[0].lower()


TITLES: dict[str, dict[str, str]] = {
    "freeze_critical": {
        "en": "JARVIS — Freeze Warning", "fr": "JARVIS — Alerte de gel",
        "de": "JARVIS — Frostwarnung", "es": "JARVIS — Alerta de heladas",
        "it": "JARVIS — Allerta gelo", "nl": "JARVIS — Vorstwaarschuwing",
        "pt": "JARVIS — Alerta de gelo",
    },
    "freeze_warning": {
        "en": "JARVIS — Temperature Alert", "fr": "JARVIS — Alerte température",
        "de": "JARVIS — Temperaturwarnung", "es": "JARVIS — Alerta de temperatura",
        "it": "JARVIS — Allerta temperatura", "nl": "JARVIS — Temperatuuralarm",
        "pt": "JARVIS — Alerta de temperatura",
    },
    "intrusion_investigating": {
        "en": "JARVIS — Security Alert", "fr": "JARVIS — Alerte de sécurité",
        "de": "JARVIS — Sicherheitswarnung", "es": "JARVIS — Alerta de seguridad",
        "it": "JARVIS — Allerta sicurezza", "nl": "JARVIS — Beveiligingsalarm",
        "pt": "JARVIS — Alerta de segurança",
    },
    "intrusion_confirmed": {
        "en": "JARVIS — INTRUSION", "fr": "JARVIS — INTRUSION",
        "de": "JARVIS — EINBRUCH", "es": "JARVIS — INTRUSIÓN",
        "it": "JARVIS — INTRUSIONE", "nl": "JARVIS — INBRAAK",
        "pt": "JARVIS — INTRUSÃO",
    },
    "intrusion_away": {
        "en": "JARVIS — Security Alert", "fr": "JARVIS — Alerte de sécurité",
        "de": "JARVIS — Sicherheitswarnung", "es": "JARVIS — Alerta de seguridad",
        "it": "JARVIS — Allerta sicurezza", "nl": "JARVIS — Beveiligingsalarm",
        "pt": "JARVIS — Alerta de segurança",
    },
    "intrusion_sleep": {
        "en": "JARVIS — Motion Detected", "fr": "JARVIS — Mouvement détecté",
        "de": "JARVIS — Bewegung erkannt", "es": "JARVIS — Movimiento detectado",
        "it": "JARVIS — Movimento rilevato", "nl": "JARVIS — Beweging gedetecteerd",
        "pt": "JARVIS — Movimento detetado",
    },
    "lockdown": {
        "en": "JARVIS — House Secured", "fr": "JARVIS — Maison sécurisée",
        "de": "JARVIS — Haus gesichert", "es": "JARVIS — Casa asegurada",
        "it": "JARVIS — Casa protetta", "nl": "JARVIS — Huis beveiligd",
        "pt": "JARVIS — Casa protegida",
    },
}


MESSAGES: dict[str, dict[str, str]] = {
    "freeze_critical": {
        "en": "{honorific}, outdoor temperature has dropped to {reading}. Pipe freeze risk is severe. I recommend opening cabinet doors near exterior walls and confirming heat is set to at least {set_to}.",
        "fr": "{honorific}, la température extérieure est descendue à {reading}. Le risque de gel des canalisations est élevé. Je recommande d'ouvrir les portes des placards le long des murs extérieurs et de régler le chauffage à au moins {set_to}.",
        "de": "{honorific}, die Außentemperatur ist auf {reading} gefallen. Es besteht ein hohes Risiko eingefrorener Rohre. Ich empfehle, Schranktüren an Außenwänden zu öffnen und die Heizung auf mindestens {set_to} einzustellen.",
        "es": "{honorific}, la temperatura exterior ha bajado a {reading}. El riesgo de congelación de tuberías es alto. Recomiendo abrir las puertas de los armarios junto a las paredes exteriores y ajustar la calefacción al menos a {set_to}.",
        "it": "{honorific}, la temperatura esterna è scesa a {reading}. Il rischio di congelamento delle tubature è elevato. Consiglio di aprire gli sportelli dei mobiletti lungo le pareti esterne e di impostare il riscaldamento ad almeno {set_to}.",
        "nl": "{honorific}, de buitentemperatuur is gedaald tot {reading}. Er is een hoog risico op bevriezing van leidingen. Ik raad aan om kastdeuren bij buitenmuren te openen en de verwarming op minstens {set_to} te zetten.",
        "pt": "{honorific}, a temperatura exterior desceu para {reading}. O risco de congelamento das tubagens é elevado. Recomendo abrir as portas dos armários junto às paredes exteriores e definir o aquecimento para pelo menos {set_to}.",
    },
    "freeze_warning": {
        "en": "{honorific}, outdoor temperature is {reading}. I'm monitoring for pipe freeze risk.",
        "fr": "{honorific}, la température extérieure est de {reading}. Je surveille le risque de gel des canalisations.",
        "de": "{honorific}, die Außentemperatur beträgt {reading}. Ich überwache das Risiko eingefrorener Rohre.",
        "es": "{honorific}, la temperatura exterior es de {reading}. Estoy vigilando el riesgo de congelación de tuberías.",
        "it": "{honorific}, la temperatura esterna è di {reading}. Sto monitorando il rischio di congelamento delle tubature.",
        "nl": "{honorific}, de buitentemperatuur is {reading}. Ik houd het risico op bevriezing van leidingen in de gaten.",
        "pt": "{honorific}, a temperatura exterior é de {reading}. Estou a monitorizar o risco de congelamento das tubagens.",
    },
    "intrusion_alert": {
        "en": "{honorific}, motion at {where} while no one is home{ctx}. Investigating from the point of entry — I'll alert the house and every device only if it's a real intrusion.",
        "fr": "{honorific}, mouvement détecté à {where} alors que personne n'est à la maison{ctx}. J'enquête depuis le point d'entrée — je n'alerterai la maison et tous les appareils que s'il s'agit d'une véritable intrusion.",
        "de": "{honorific}, Bewegung bei {where}, während niemand zu Hause ist{ctx}. Ich untersuche vom Eintrittspunkt aus — ich alarmiere das Haus und alle Geräte nur bei einem echten Einbruch.",
        "es": "{honorific}, movimiento en {where} cuando no hay nadie en casa{ctx}. Estoy investigando desde el punto de entrada; solo alertaré a la casa y a todos los dispositivos si es una intrusión real.",
        "it": "{honorific}, movimento a {where} mentre non c'è nessuno in casa{ctx}. Sto indagando dal punto di ingresso — avviserò la casa e tutti i dispositivi solo se si tratta di una vera intrusione.",
        "nl": "{honorific}, beweging bij {where} terwijl er niemand thuis is{ctx}. Ik onderzoek het vanaf het toegangspunt — ik waarschuw het huis en alle apparaten alleen als het een echte inbraak is.",
        "pt": "{honorific}, movimento em {where} enquanto não está ninguém em casa{ctx}. Estou a investigar a partir do ponto de entrada — só alertarei a casa e todos os dispositivos se for uma intrusão real.",
    },
    "intrusion_ctx_open": {
        "en": " ({name} open)", "fr": " ({name} ouvert)", "de": " ({name} offen)",
        "es": " ({name} abierto)", "it": " ({name} aperto)",
        "nl": " ({name} open)", "pt": " ({name} aberto)",
    },
    "intrusion_ctx_armed": {
        "en": " (alarm armed)", "fr": " (alarme armée)", "de": " (Alarm scharf)",
        "es": " (alarma armada)", "it": " (allarme inserito)",
        "nl": " (alarm ingeschakeld)", "pt": " (alarme armado)",
    },
    "lockdown_lifted": {
        "en": "{honorific}, lockdown lifted. The house is back to normal.",
        "fr": "{honorific}, confinement levé. La maison est revenue à la normale.",
        "de": "{honorific}, Sicherung aufgehoben. Das Haus ist wieder im Normalzustand.",
        "es": "{honorific}, confinamiento desactivado. La casa ha vuelto a la normalidad.",
        "it": "{honorific}, blocco revocato. La casa è tornata alla normalità.",
        "nl": "{honorific}, vergrendeling opgeheven. Het huis is weer normaal.",
        "pt": "{honorific}, confinamento levantado. A casa voltou ao normal.",
    },
    "lockdown_already_secured": {
        "en": "{honorific}, lockdown engaged — the home was already fully secured.",
        "fr": "{honorific}, confinement activé — la maison était déjà entièrement sécurisée.",
        "de": "{honorific}, Sicherung aktiviert — das Haus war bereits vollständig gesichert.",
        "es": "{honorific}, confinamiento activado — la casa ya estaba totalmente asegurada.",
        "it": "{honorific}, blocco attivato — la casa era già completamente protetta.",
        "nl": "{honorific}, vergrendeling ingeschakeld — het huis was al volledig beveiligd.",
        "pt": "{honorific}, confinamento ativado — a casa já estava totalmente protegida.",
    },
    # ── Composed lockdown pieces (device lists) ─────────────────────────────
    # Verb phrases: {names} is a comma-joined device list and always follows the
    # verb, so past participles stay invariable (no gender agreement with the
    # device). de/nl place the participle after the object, as the grammar wants.
    "lockdown_locked": {
        "en": "locked {names}", "fr": "verrouillé {names}",
        "de": "{names} verriegelt", "es": "bloqueado {names}",
        "it": "bloccato {names}", "nl": "{names} vergrendeld",
        "pt": "tranquei {names}",
    },
    "lockdown_closed": {
        "en": "closed {names}", "fr": "fermé {names}",
        "de": "{names} geschlossen", "es": "cerrado {names}",
        "it": "chiuso {names}", "nl": "{names} gesloten",
        "pt": "fechei {names}",
    },
    # Gap clauses — openings that can't be secured remotely. Phrased to avoid any
    # adjective/pronoun agreeing with the (unknown-gender) device: impersonal /
    # infinitive forms, so one wording is correct whatever the device is.
    "lockdown_gap_one": {
        "en": "{names} is open and I can't secure it remotely — you'll want to close it",
        "fr": "{names} : impossible à verrouiller à distance, à fermer manuellement",
        "de": "{names}: lässt sich nicht aus der Ferne sichern — bitte manuell schließen",
        "es": "{names}: no se puede asegurar a distancia — hay que cerrarlo manualmente",
        "it": "{names}: impossibile bloccare a distanza — da chiudere manualmente",
        "nl": "{names}: niet op afstand te vergrendelen — graag handmatig sluiten",
        "pt": "{names}: não dá para trancar à distância — feche manualmente",
    },
    "lockdown_gap_few": {
        "en": "{names} are open and I can't secure them remotely — you'll want to close them",
        "fr": "{names} : impossible à verrouiller à distance, à fermer manuellement",
        "de": "{names}: lassen sich nicht aus der Ferne sichern — bitte manuell schließen",
        "es": "{names}: no se pueden asegurar a distancia — hay que cerrarlos manualmente",
        "it": "{names}: impossibile bloccare a distanza — da chiudere manualmente",
        "nl": "{names}: niet op afstand te vergrendelen — graag handmatig sluiten",
        "pt": "{names}: não dá para trancar à distância — feche manualmente",
    },
    "lockdown_gap_many": {
        "en": "{count} openings are open and I can't secure them remotely — you'll want to close them",
        "fr": "{count} ouvertures ne peuvent pas être verrouillées à distance — à fermer manuellement",
        "de": "{count} Öffnungen lassen sich nicht aus der Ferne sichern — bitte manuell schließen",
        "es": "{count} aberturas no se pueden asegurar a distancia — hay que cerrarlas manualmente",
        "it": "{count} aperture non si possono bloccare a distanza — da chiudere manualmente",
        "nl": "{count} openingen zijn niet op afstand te vergrendelen — graag handmatig sluiten",
        "pt": "{count} aberturas não podem ser trancadas à distância — feche manualmente",
    },
    # Wrappers. The auxiliary ("I", "j'ai", "ich habe", …) lives in the wrapper
    # so {did} — the joined verb phrases — reads naturally after it; pt uses the
    # simple past and takes no auxiliary.
    "lockdown_did": {
        "en": "{honorific}, lockdown engaged — I {did}. The home is secure.",
        "fr": "{honorific}, confinement activé — j'ai {did}. La maison est sécurisée.",
        "de": "{honorific}, Sicherung aktiviert — ich habe {did}. Das Haus ist gesichert.",
        "es": "{honorific}, confinamiento activado — he {did}. La casa está asegurada.",
        "it": "{honorific}, blocco attivato — ho {did}. La casa è protetta.",
        "nl": "{honorific}, vergrendeling ingeschakeld — ik heb {did}. Het huis is beveiligd.",
        "pt": "{honorific}, confinamento ativado — {did}. A casa está segura.",
    },
    "lockdown_did_gap": {
        "en": "{honorific}, lockdown engaged — I {did}, but {gap}.",
        "fr": "{honorific}, confinement activé — j'ai {did}, mais {gap}.",
        "de": "{honorific}, Sicherung aktiviert — ich habe {did}, aber {gap}.",
        "es": "{honorific}, confinamiento activado — he {did}, pero {gap}.",
        "it": "{honorific}, blocco attivato — ho {did}, ma {gap}.",
        "nl": "{honorific}, vergrendeling ingeschakeld — ik heb {did}, maar {gap}.",
        "pt": "{honorific}, confinamento ativado — {did}, mas {gap}.",
    },
    "lockdown_gap_only": {
        "en": "{honorific}, lockdown engaged. Everything was already secured, but {gap}.",
        "fr": "{honorific}, confinement activé. Tout était déjà sécurisé, mais {gap}.",
        "de": "{honorific}, Sicherung aktiviert. Alles war bereits gesichert, aber {gap}.",
        "es": "{honorific}, confinamiento activado. Todo ya estaba asegurado, pero {gap}.",
        "it": "{honorific}, blocco attivato. Era già tutto protetto, ma {gap}.",
        "nl": "{honorific}, vergrendeling ingeschakeld. Alles was al beveiligd, maar {gap}.",
        "pt": "{honorific}, confinamento ativado. Tudo já estava seguro, mas {gap}.",
    },
    "lockdown_nighttime": {
        "en": "{honorific}, nighttime lockdown: {body}. The house is secured.",
        "fr": "{honorific}, confinement nocturne : {body}. La maison est sécurisée.",
        "de": "{honorific}, nächtliche Sicherung: {body}. Das Haus ist gesichert.",
        "es": "{honorific}, confinamiento nocturno: {body}. La casa está asegurada.",
        "it": "{honorific}, blocco notturno: {body}. La casa è protetta.",
        "nl": "{honorific}, nachtelijke vergrendeling: {body}. Het huis is beveiligd.",
        "pt": "{honorific}, confinamento noturno: {body}. A casa está segura.",
    },
}


def title(key: str, lang: str | None) -> str:
    """Localized notification title for ``key`` (English fallback)."""
    table = TITLES.get(key, {})
    return table.get(_norm(lang)) or table.get(_FALLBACK) or "JARVIS"


def message(key: str, lang: str | None, **params) -> str:
    """Localized, parameter-filled message for ``key`` (English fallback).

    Never raises: an unknown key or a formatting problem yields the English
    template (or an empty string) rather than propagating.
    """
    table = MESSAGES.get(key, {})
    tmpl = table.get(_norm(lang)) or table.get(_FALLBACK) or ""
    try:
        return tmpl.format(**params)
    except Exception:
        try:
            return (table.get(_FALLBACK, "") or "").format(**params)
        except Exception:
            return table.get(_FALLBACK, "") or ""


# Coordinating conjunction per language, for natural-language device lists.
_AND: dict[str, str] = {
    "en": "and", "fr": "et", "de": "und", "es": "y",
    "it": "e", "nl": "en", "pt": "e",
}
# Languages that put a comma before the final conjunction (the "Oxford comma").
_OXFORD = {"en"}


def join_names(names, lang: str | None = None) -> str:
    """Natural-language list join with the localized conjunction:
    ['a'] -> 'a'; ['a','b'] -> 'a et b' (fr); ['a','b','c'] -> 'a, b, and c' (en).
    Device/area names pass through untranslated."""
    names = [n for n in (names or []) if n]
    a = _AND.get(_norm(lang), "and")
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} {a} {names[1]}"
    sep = f", {a} " if _norm(lang) in _OXFORD else f" {a} "
    return f"{', '.join(names[:-1])}{sep}{names[-1]}"
