# Filtrelenecek Türkçe küfür ve argo kelime listesi
PROFANITY_LIST = [
    "amk", "aq", "amq", "sik", "sikerim", "yarak", "yarrak", 
    "orospu", "piç", "kahpe", "göt", "ibne", "amcık", "puşt"
]

def contains_profanity(text: str) -> bool:
    if not text:
        return False
    words = text.lower().split()
    for word in words:
        clean_word = ''.join(e for e in word if e.isalnum())
        if clean_word in PROFANITY_LIST:
            return True
    return False
