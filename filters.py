import re

# Genişletilmiş Türkçe Küfür, Argo ve Hakaret Listesi (Türevleri ve Kombinasyonlarıyla)
PROFANITY_LIST = [
    "amk", "aq", "amq", "amcık", "amcik", "amciga", "amcığa", "amcıklar",
    "sik", "sikerim", "siktim", "sikiş", "sikis", "sikik", "siksik", "sikici", "sokuk", "sokan",
    "yarak", "yarrak", "yarraga", "yarraka", "yarrag", "yarrak kafali",
    "orospu", "orosbu", "orospucocugu", "orospu çocuğu", "orospunun", "orospular",
    "piç", "pic", "pich", "piç kurusu", "pickurusu",
    "kahpe", "kahpenin", "kahpeler",
    "göt", "got", "götveren", "gotveren", "göt deliği", "götlale",
    "ibne", "ibneler", "ibnesin", "İbne",
    "puşt", "pust", "pustlar",
    "amoglu", "amoyuncugu", "amınakoyim", "aminakoyim", "amina koyum", "amina koyayim",
    "ananı", "anani", "ananın", "ananin", "anı sikeyim", "anı",
    "siktir", "siktirgit", "siktir lan",
    "amına", "amina", "aminakoydugum", "amınakoyduğum",
    "orosbunun", "oç", "oc", "o.ç.",
    "yarrak kafali", "yarrakağacı", "sik kafalı", "göt lalesi",
    "sikis", "sikişelim", "sikişvar", "kes sesini", "amını", "amini",
    "amina kodugum", "amcik torbasi", "amcık hoşafı", "yarram", "yarragim",
    "siktiğimin", "siktigimin", "sikeli", "sikisek", "soyunu sikeyim"
]

def normalize_text(text):
    if not text or not isinstance(text, str):
        return ""
    
    text = text.lower()
    
    # Leet speak ve harf oyunlarını normalize et (Kombinasyonları yakalamak için)
    replacements = {
        '0': 'a', '@': 'a', '4': 'a', 'â': 'a',
        '1': 'i', '!': 'i', 'İ': 'i', 'ı': 'i',
        '3': 'e', 'ê': 'e',
        '5': 's', '$': 's',
        '7': 't',
        '9': 'g',
        '8': 'b',
        '2': 'z',
        '6': 'g'
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
        
    # Şekilli sembolleri, noktalama ve boşluk fazlalıklarını temizle
    text = re.sub(r'[^a-zçğöşü0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# Ağır İhlaller, 18 Yaş Altı / Çocuk İstismarı, Takas ve Link Kalıpları (Regex)
BANNED_PATTERNS = [
    # 18 yaş altı / rüşt olmayan / çocuk istismarı arayışları (Tüm türevleri)
    r'\b(1[0-7]|0?[0-8])\s*(yaş|yas|yş)\b',
    r'\b(cocuk|çocuk|bebek|korpe|körpe|ogrenci|öğrenci|lise|ortaokul|ilkokul)\b',
    r'\b(resit|reşit)\s*(degil|değil|olmayan)\b',
    r'\b(kucuk|küçük)\s*(yas|yaş|kiz|kız|erkek)\b',
    r'\b(cp|csam|pedofili|porno|nsfw|hentai|ifsa|ifşaa)\b',

    # Takas ve yasadışı ticaret içerikleri
    r'\b(takas|tks|satilik|satılık|kiralik|kiralık|fiyat|ucret|ücret|ibanda|papara|paparda)\b',
]

def contains_profanity(text: str) -> bool:
    if not text:
        return False
        
    raw = str(text)
    normalized = normalize_text(raw)

    # 1. Her türlü dış link, domain, www, t.me, sosyal medya yönlendirme tespiti
    url_regex = r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9-_]+\.(com|net|org|io|co|me|xyz|biz|info|ru|tk|ml|ga|cf|de|fr|t\.me|telegram\.me|discord\.gg|whatsapp\.com|linktr\.ee|bit\.ly))'
    if re.search(url_regex, raw, re.IGNORECASE):
        return True

    # 2. Gelişmiş Regex kalıp kontrolü (18 yaş altı, çocuk istismarı, takas vs.)
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, raw, re.IGNORECASE) or re.search(pattern, normalized, re.IGNORECASE):
            return True

    # 3. Kelime bazlı genişletilmiş küfür ve argo listesi taraması (Kombinasyonlu)
    words = normalized.split()
    for word in words:
        clean_word = ''.join(e for e in word if e.isalnum())
        if clean_word in PROFANITY_LIST:
            return True
            
    # İki kelimelik birleşik küfür kombinasyonları kontrolü için tam metin taraması
    for profanity in PROFANITY_LIST:
        if " " in profanity and profanity in normalized:
            return True

    return False
