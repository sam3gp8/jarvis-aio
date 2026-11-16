"""Localized templates for JARVIS's anticipation / learned-routine alerts (#539).

The cognition engine's proactive mentions — "the garage is open, it's usually
closed by now", "around this time you usually …", "X is usually home by 18:00" —
are built WITHOUT the LLM, so the agent's language directive never touched them:
a household on Italian got them in hard-coded English, as push and as speech.
Like ``notify_i18n`` (safety notifications) and ``local_i18n`` (the local intent
engine), this is a static per-language table: no API call, no added latency.

English is the base and the fallback for any language or key not present.
Entity names, people's names, calendar titles and spoken commands are passed
through as parameters and never translated.

Two things keep the non-English text natural without knowing an entity's
grammatical gender (a name like "Box IMOU" or "Porta garage" carries none):

* a state is shown as a quoted label («aperto») rather than as a predicate
  adjective that would have to agree with the name; and
* phrasings avoid participles that agree with a person's gender ("rentré(e)",
  "вернулся/вернулась").

Coverage rule (same as ``notify_i18n``): every language appears in EVERY table,
so a notification is never half-translated; a test enforces it.
"""
from __future__ import annotations

_FALLBACK = "en"
LANGS = ("en", "fr", "de", "es", "it", "nl", "pt", "ru", "uk", "pl")


def _norm(lang: str | None) -> str:
    return (lang or "en").split("-")[0].lower()


MESSAGES: dict[str, dict[str, str]] = {
    # An entity is in a state that is unusual for this hour.
    "unusual_state": {
        "en": "{name} is {state}. Around this time it's usually {usual}, so I thought it worth mentioning.",
        "fr": "{name} est actuellement « {state} ». À cette heure-ci, c'est habituellement « {usual} » — j'ai pensé qu'il valait mieux vous le signaler.",
        "de": "{name} ist gerade „{state}“. Um diese Zeit ist das normalerweise „{usual}“ – ich dachte, das sollten Sie wissen.",
        "es": "{name} está ahora «{state}». A esta hora suele estar «{usual}», así que he pensado que convenía avisarle.",
        "it": "{name} è ora «{state}». A quest'ora di solito è «{usual}», quindi ho pensato fosse il caso di segnalarlo.",
        "nl": "{name} is nu ‘{state}’. Rond deze tijd is dat meestal ‘{usual}’, dus ik vond het het melden waard.",
        "pt": "{name} está agora «{state}». A esta hora costuma estar «{usual}», por isso achei que valia a pena avisar.",
        "ru": "{name}: сейчас «{state}». В это время обычно «{usual}» — я решил, что об этом стоит сообщить.",
        "uk": "{name}: зараз «{state}». О цій порі зазвичай «{usual}» — я вирішив, що про це варто повідомити.",
        "pl": "{name}: teraz „{state}”. O tej porze zwykle „{usual}” — uznałem, że warto o tym wspomnieć.",
    },
    # A recurring daily activity has not happened yet, past its usual time.
    "overdue_activity": {
        "en": "{name} usually has activity by around {time}, but there's been none yet today — thought it worth flagging.",
        "fr": "{name} montre habituellement de l'activité vers {time}, mais il n'y en a pas encore eu aujourd'hui — j'ai préféré vous le signaler.",
        "de": "Bei {name} gibt es normalerweise bis etwa {time} Aktivität, heute aber noch keine – ich wollte darauf hinweisen.",
        "es": "{name} suele tener actividad hacia las {time}, pero hoy aún no ha habido ninguna; me ha parecido oportuno avisarle.",
        "it": "Di solito {name} registra attività entro le {time} circa, ma oggi non ce n'è ancora stata: ho pensato fosse il caso di segnalarlo.",
        "nl": "{name} heeft meestal activiteit rond {time}, maar vandaag is er nog geen geweest — ik wilde het even melden.",
        "pt": "{name} costuma ter atividade por volta das {time}, mas hoje ainda não houve nenhuma — achei que valia a pena avisar.",
        "ru": "У «{name}» обычно есть активность примерно к {time}, но сегодня её ещё не было — решил сообщить.",
        "uk": "У «{name}» зазвичай є активність приблизно до {time}, але сьогодні її ще не було — вирішив повідомити.",
        "pl": "{name} zwykle wykazuje aktywność około {time}, ale dziś jeszcze jej nie było — uznałem, że warto to zgłosić.",
    },
    # A learned routine: a device usually changes state around now.
    "routine_device": {
        "en": "Around this time, {name} usually switches to {state}.",
        "fr": "À cette heure-ci, {name} passe habituellement à « {state} ».",
        "de": "Um diese Zeit wechselt {name} normalerweise auf „{state}“.",
        "es": "A esta hora, {name} suele pasar a «{state}».",
        "it": "A quest'ora {name} di solito passa a «{state}».",
        "nl": "Rond deze tijd schakelt {name} meestal naar ‘{state}’.",
        "pt": "A esta hora, {name} costuma passar a «{state}».",
        "ru": "В это время «{name}» обычно переходит в состояние «{state}».",
        "uk": "О цій порі «{name}» зазвичай переходить у стан «{state}».",
        "pl": "O tej porze {name} zwykle przechodzi w stan „{state}”.",
    },
    # A learned routine: the person usually gives this command around now.
    "routine_command": {
        "en": "Around this time you usually ask me: “{command}”.",
        "fr": "À cette heure-ci, vous me demandez habituellement : « {command} ».",
        "de": "Um diese Zeit bitten Sie mich normalerweise: „{command}“.",
        "es": "A esta hora suele pedirme: «{command}».",
        "it": "A quest'ora di solito mi chiede: «{command}».",
        "nl": "Rond deze tijd vraagt u mij meestal: ‘{command}’.",
        "pt": "A esta hora costuma pedir-me: «{command}».",
        "ru": "В это время вы обычно просите меня: «{command}».",
        "uk": "О цій порі ви зазвичай просите мене: «{command}».",
        "pl": "O tej porze zwykle pada polecenie: „{command}”.",
    },
    # A learned routine of a kind this table has no sentence for: the stored
    # description is shown as-is behind a localized lead-in.
    "routine_other": {
        "en": "Around this time, usually: {routine}.",
        "fr": "À cette heure-ci, d'habitude : {routine}.",
        "de": "Um diese Zeit üblich: {routine}.",
        "es": "A esta hora, lo habitual: {routine}.",
        "it": "A quest'ora, di solito: {routine}.",
        "nl": "Rond deze tijd gebruikelijk: {routine}.",
        "pt": "A esta hora, o habitual: {routine}.",
        "ru": "В это время обычно: {routine}.",
        "uk": "О цій порі зазвичай: {routine}.",
        "pl": "O tej porze zwykle: {routine}.",
    },
    # A person is still home past their usual departure time.
    "still_home": {
        "en": "{name} is usually out by around {time}, but is still home — thought you'd want to know.",
        "fr": "D'habitude, {name} a déjà quitté la maison vers {time}, mais s'y trouve encore — j'ai pensé que vous voudriez le savoir.",
        "de": "{name} ist normalerweise gegen {time} schon aus dem Haus, aber noch da – ich dachte, das möchten Sie wissen.",
        "es": "{name} suele haber salido hacia las {time}, pero sigue en casa; he pensado que querría saberlo.",
        "it": "Di solito {name} esce di casa entro le {time} circa, ma è ancora a casa: ho pensato volesse saperlo.",
        "nl": "{name} is meestal rond {time} al de deur uit, maar is nog thuis — ik dacht dat u dat wilde weten.",
        "pt": "{name} costuma já ter saído por volta das {time}, mas ainda está em casa — achei que gostaria de saber.",
        "ru": "{name} обычно уходит из дома примерно к {time}, но всё ещё дома — подумал, что вам стоит знать.",
        "uk": "{name} зазвичай виходить з дому приблизно до {time}, але досі вдома — подумав, що вам варто знати.",
        "pl": "{name} zwykle wychodzi z domu około {time}, ale nadal jest w domu — uznałem, że warto o tym wiedzieć.",
    },
    # A person is not back yet, past their usual arrival time.
    "not_back": {
        "en": "{name} is usually home by around {time}, but isn't back yet.",
        "fr": "D'habitude, {name} est à la maison vers {time}, mais n'est pas encore de retour.",
        "de": "{name} ist normalerweise gegen {time} zu Hause, aber noch nicht zurück.",
        "es": "{name} suele estar en casa hacia las {time}, pero aún no ha vuelto.",
        "it": "Di solito {name} è a casa entro le {time} circa, ma non è ancora di ritorno.",
        "nl": "{name} is meestal rond {time} thuis, maar is nog niet terug.",
        "pt": "{name} costuma estar em casa por volta das {time}, mas ainda não regressou.",
        "ru": "{name} обычно дома примерно к {time}, но пока ещё не дома.",
        "uk": "{name} зазвичай удома приблизно до {time}, але поки що не вдома.",
        "pl": "{name} zwykle jest w domu około {time}, ale wciąż jest poza domem.",
    },
    # Time to leave for a calendar event.
    "departure": {
        "en": "Heads up — {event} begins in about {minutes} minutes; you'll want to head out.",
        "fr": "À noter : {event} commence dans environ {minutes} minutes ; il serait temps de partir.",
        "de": "Hinweis: {event} beginnt in etwa {minutes} Minuten; Sie sollten sich auf den Weg machen.",
        "es": "Aviso: {event} empieza en unos {minutes} minutos; conviene ir saliendo.",
        "it": "Promemoria: {event} inizia tra circa {minutes} minuti; conviene avviarsi.",
        "nl": "Let op: {event} begint over ongeveer {minutes} minuten; u kunt maar beter vertrekken.",
        "pt": "Atenção: {event} começa dentro de cerca de {minutes} minutos; convém ir saindo.",
        "ru": "Напоминание: «{event}» начинается примерно через {minutes} мин; пора выходить.",
        "uk": "Нагадування: «{event}» починається приблизно через {minutes} хв; час виходити.",
        "pl": "Przypomnienie: {event} zaczyna się za około {minutes} min; pora wychodzić.",
    },
    # …the same, with the event's location. Outside English the location goes in
    # brackets, since the preposition would depend on the place.
    "departure_at": {
        "en": "Heads up — {event} at {location} begins in about {minutes} minutes; you'll want to head out.",
        "fr": "À noter : {event} ({location}) commence dans environ {minutes} minutes ; il serait temps de partir.",
        "de": "Hinweis: {event} ({location}) beginnt in etwa {minutes} Minuten; Sie sollten sich auf den Weg machen.",
        "es": "Aviso: {event} ({location}) empieza en unos {minutes} minutos; conviene ir saliendo.",
        "it": "Promemoria: {event} ({location}) inizia tra circa {minutes} minuti; conviene avviarsi.",
        "nl": "Let op: {event} ({location}) begint over ongeveer {minutes} minuten; u kunt maar beter vertrekken.",
        "pt": "Atenção: {event} ({location}) começa dentro de cerca de {minutes} minutos; convém ir saindo.",
        "ru": "Напоминание: «{event}» ({location}) начинается примерно через {minutes} мин; пора выходить.",
        "uk": "Нагадування: «{event}» ({location}) починається приблизно через {minutes} хв; час виходити.",
        "pl": "Przypomnienie: {event} ({location}) zaczyna się za około {minutes} min; pora wychodzić.",
    },
    # A person is on the way home.
    "arriving": {
        "en": "{name} is heading home — about {distance} km out.",
        "fr": "{name} est sur le chemin du retour — à environ {distance} km.",
        "de": "{name} ist auf dem Heimweg – noch etwa {distance} km entfernt.",
        "es": "{name} va de camino a casa: está a unos {distance} km.",
        "it": "{name} sta tornando a casa: è a circa {distance} km.",
        "nl": "{name} is onderweg naar huis — nog ongeveer {distance} km.",
        "pt": "{name} está a caminho de casa — a cerca de {distance} km.",
        "ru": "{name} едет домой — осталось около {distance} км.",
        "uk": "{name} прямує додому — залишилося близько {distance} км.",
        "pl": "{name} wraca do domu — jeszcze około {distance} km.",
    },
}

# Stand-in for a calendar event with no title.
AN_EVENT: dict[str, str] = {
    "en": "an event", "fr": "un événement", "de": "ein Termin", "es": "un evento",
    "it": "un evento", "nl": "een afspraak", "pt": "um evento", "ru": "событие",
    "uk": "подія", "pl": "wydarzenie",
}

# Home Assistant state values that appear inside the messages above. English maps
# each token to the word JARVIS already used; a token not listed is shown as-is
# with underscores turned to spaces (a media player's "buffering", a custom
# sensor state). Other languages give a label that reads naturally inside quotes.
STATES: dict[str, dict[str, str]] = {
    "en": {
        "on": "on", "off": "off", "open": "open", "closed": "closed",
        "opening": "opening", "closing": "closing", "locked": "locked",
        "unlocked": "unlocked", "home": "home", "not_home": "not home",
        "idle": "idle", "playing": "playing", "paused": "paused",
        "standby": "standby", "heat": "heat", "cool": "cool", "auto": "auto",
        "armed_home": "armed home", "armed_away": "armed away",
        "armed_night": "armed night", "disarmed": "disarmed",
        "docked": "docked", "cleaning": "cleaning",
        "away": "away", "active": "active", "charging": "charging",
    },
    "fr": {
        "on": "allumé", "off": "éteint", "open": "ouvert", "closed": "fermé",
        "opening": "en ouverture", "closing": "en fermeture", "locked": "verrouillé",
        "unlocked": "déverrouillé", "home": "à la maison", "not_home": "absent",
        "idle": "inactif", "playing": "en lecture", "paused": "en pause",
        "standby": "en veille", "heat": "chauffage", "cool": "climatisation",
        "auto": "automatique", "armed_home": "armé (présent)",
        "armed_away": "armé (absent)", "armed_night": "armé (nuit)",
        "disarmed": "désarmé", "docked": "à la base", "cleaning": "en nettoyage",
        "away": "absent", "active": "actif", "charging": "en charge",
    },
    "de": {
        "on": "an", "off": "aus", "open": "offen", "closed": "geschlossen",
        "opening": "öffnet", "closing": "schließt", "locked": "verriegelt",
        "unlocked": "entriegelt", "home": "zu Hause", "not_home": "abwesend",
        "idle": "inaktiv", "playing": "Wiedergabe", "paused": "pausiert",
        "standby": "Standby", "heat": "Heizen", "cool": "Kühlen",
        "auto": "Automatik", "armed_home": "scharf (zu Hause)",
        "armed_away": "scharf (abwesend)", "armed_night": "scharf (Nacht)",
        "disarmed": "unscharf", "docked": "an der Station", "cleaning": "reinigt",
        "away": "abwesend", "active": "aktiv", "charging": "lädt",
    },
    "es": {
        "on": "encendido", "off": "apagado", "open": "abierto", "closed": "cerrado",
        "opening": "abriéndose", "closing": "cerrándose", "locked": "bloqueado",
        "unlocked": "desbloqueado", "home": "en casa", "not_home": "fuera de casa",
        "idle": "inactivo", "playing": "reproduciendo", "paused": "en pausa",
        "standby": "en espera", "heat": "calefacción", "cool": "refrigeración",
        "auto": "automático", "armed_home": "armado (en casa)",
        "armed_away": "armado (ausente)", "armed_night": "armado (noche)",
        "disarmed": "desarmado", "docked": "en la base", "cleaning": "limpiando",
        "away": "ausente", "active": "activo", "charging": "cargando",
    },
    "it": {
        "on": "acceso", "off": "spento", "open": "aperto", "closed": "chiuso",
        "opening": "in apertura", "closing": "in chiusura", "locked": "bloccato",
        "unlocked": "sbloccato", "home": "a casa", "not_home": "fuori casa",
        "idle": "inattivo", "playing": "in riproduzione", "paused": "in pausa",
        "standby": "in standby", "heat": "riscaldamento", "cool": "raffrescamento",
        "auto": "automatico", "armed_home": "inserito (in casa)",
        "armed_away": "inserito (fuori casa)", "armed_night": "inserito (notte)",
        "disarmed": "disinserito", "docked": "alla base", "cleaning": "in pulizia",
        "away": "assente", "active": "attivo", "charging": "in carica",
    },
    "nl": {
        "on": "aan", "off": "uit", "open": "open", "closed": "gesloten",
        "opening": "gaat open", "closing": "gaat dicht", "locked": "vergrendeld",
        "unlocked": "ontgrendeld", "home": "thuis", "not_home": "afwezig",
        "idle": "inactief", "playing": "speelt af", "paused": "gepauzeerd",
        "standby": "stand-by", "heat": "verwarmen", "cool": "koelen",
        "auto": "automatisch", "armed_home": "ingeschakeld (thuis)",
        "armed_away": "ingeschakeld (afwezig)", "armed_night": "ingeschakeld (nacht)",
        "disarmed": "uitgeschakeld", "docked": "op het station", "cleaning": "maakt schoon",
        "away": "afwezig", "active": "actief", "charging": "aan het opladen",
    },
    "pt": {
        "on": "ligado", "off": "desligado", "open": "aberto", "closed": "fechado",
        "opening": "a abrir", "closing": "a fechar", "locked": "trancado",
        "unlocked": "destrancado", "home": "em casa", "not_home": "fora de casa",
        "idle": "inativo", "playing": "a reproduzir", "paused": "em pausa",
        "standby": "em espera", "heat": "aquecimento", "cool": "arrefecimento",
        "auto": "automático", "armed_home": "armado (em casa)",
        "armed_away": "armado (ausente)", "armed_night": "armado (noite)",
        "disarmed": "desarmado", "docked": "na base", "cleaning": "a limpar",
        "away": "ausente", "active": "ativo", "charging": "a carregar",
    },
    "ru": {
        "on": "включено", "off": "выключено", "open": "открыто", "closed": "закрыто",
        "opening": "открывается", "closing": "закрывается", "locked": "заперто",
        "unlocked": "отперто", "home": "дома", "not_home": "не дома",
        "idle": "бездействие", "playing": "воспроизведение", "paused": "пауза",
        "standby": "ожидание", "heat": "обогрев", "cool": "охлаждение",
        "auto": "авто", "armed_home": "под охраной (дома)",
        "armed_away": "под охраной (нет дома)", "armed_night": "под охраной (ночь)",
        "disarmed": "снято с охраны", "docked": "на базе", "cleaning": "уборка",
        "away": "нет дома", "active": "активно", "charging": "зарядка",
    },
    "uk": {
        "on": "увімкнено", "off": "вимкнено", "open": "відчинено", "closed": "зачинено",
        "opening": "відчиняється", "closing": "зачиняється", "locked": "замкнено",
        "unlocked": "відімкнено", "home": "вдома", "not_home": "не вдома",
        "idle": "бездіяльність", "playing": "відтворення", "paused": "пауза",
        "standby": "очікування", "heat": "обігрів", "cool": "охолодження",
        "auto": "авто", "armed_home": "під охороною (вдома)",
        "armed_away": "під охороною (нікого немає)", "armed_night": "під охороною (ніч)",
        "disarmed": "знято з охорони", "docked": "на базі", "cleaning": "прибирання",
        "away": "немає вдома", "active": "активно", "charging": "заряджання",
    },
    "pl": {
        "on": "włączone", "off": "wyłączone", "open": "otwarte", "closed": "zamknięte",
        "opening": "otwiera się", "closing": "zamyka się", "locked": "zablokowane",
        "unlocked": "odblokowane", "home": "w domu", "not_home": "poza domem",
        "idle": "bezczynność", "playing": "odtwarzanie", "paused": "pauza",
        "standby": "czuwanie", "heat": "grzanie", "cool": "chłodzenie",
        "auto": "auto", "armed_home": "uzbrojony (w domu)",
        "armed_away": "uzbrojony (poza domem)", "armed_night": "uzbrojony (noc)",
        "disarmed": "rozbrojony", "docked": "w stacji", "cleaning": "sprzątanie",
        "away": "poza domem", "active": "aktywne", "charging": "ładowanie",
    },
}

# Device classes whose binary on/off means open/closed.
_OPENING_CLASSES = ("door", "window", "garage_door", "opening", "garage")

# Languages that write a decimal comma.
_DECIMAL_COMMA = frozenset(LANGS) - {"en"}


def message(key: str, lang: str | None, **params) -> str:
    """Localized, parameter-filled message for ``key`` (English fallback).

    Never raises: an unknown key or a formatting problem yields the English
    template (or an empty string) rather than propagating."""
    table = MESSAGES.get(key, {})
    tmpl = table.get(_norm(lang)) or table.get(_FALLBACK) or ""
    try:
        return tmpl.format(**params)
    except Exception:
        try:
            return (table.get(_FALLBACK, "") or "").format(**params)
        except Exception:
            return table.get(_FALLBACK, "") or ""


def state_word(state, lang: str | None, device_class=None) -> str:
    """A Home Assistant state as JARVIS says it in ``lang``.

    A door/window/garage binary sensor's ``on``/``off`` reads as open/closed. A
    state with no entry is shown as-is with underscores as spaces — never blank,
    never an English word swapped in for an unknown one."""
    token = str(state if state is not None else "").strip()
    if device_class in _OPENING_CLASSES:
        token = {"on": "open", "off": "closed"}.get(token, token)
    key = token.lower()
    table = STATES.get(_norm(lang)) or STATES[_FALLBACK]
    return table.get(key) or STATES[_FALLBACK].get(key) or token.replace("_", " ")


def an_event(lang: str | None) -> str:
    """The stand-in for a calendar event with no title."""
    return AN_EVENT.get(_norm(lang)) or AN_EVENT[_FALLBACK]


def number(value, lang: str | None, decimals: int = 1) -> str:
    """A number with the language's decimal mark ("3.4" in English, "3,4" in the
    others here). Never raises."""
    try:
        text = f"{float(value):.{int(decimals)}f}"
    except Exception:
        return str(value)
    return text.replace(".", ",") if _norm(lang) in _DECIMAL_COMMA else text
