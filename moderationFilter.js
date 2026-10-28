// ==========================================================================
// GOLDEN-GUARDIAN / GOLDENMATCH - GELİŞMİŞ GÜVENLİK & MODERASYON MOTORU
// ==========================================================================

// Leet speak (harf değiştirme) ve boşluk oyunlarını normalize eden fonksiyon
function normalizeText(text) {
  if (!text || typeof text !== 'string') return '';
  return text
    .toLocaleLowerCase('tr-TR')
    .replace(/[0@]/g, 'a')
    .replace(/[1!]/g, 'i')
    .replace(/[3]/g, 'e')
    .replace(/[4]/g, 'a')
    .replace(/[5]/g, 's')
    .replace(/[7]/g, 't')
    .replace(/[9]/g, 'g')
    .replace(/[8]/g, 'b')
    .replace(/[^a-zçğİıöşü0-9\s]/g, '') // Özel karakterleri ve şekilli sembolleri temizle
    .replace(/\s+/g, ' ')             // Fazla boşlukları teke düşür
    .trim();
}

// Yasaklı Kök Kalıpları ve Regex Kombinasyonları
const BANNED_PATTERNS = [
  // 18 yaş altı / çocuk istismarı / rüşt olmayan arayışlar (Tüm türevleri)
  /\b(1[0-7]|0?[0-8])\s*(yaş|yas|yş)\b/i,
  /\b(cocuk|çocuk|bebek|korpe|körpe|ogrenci|öğrenci|lise|ortaokul|ilkokul)\b/i,
  /\b(reşit|resit)\s*(degil|değil|olmayan)\b/i,
  /\b(kucuk|küçük)\s*(yas|yaş|qart|kiz|kız|erkek)\b/i,

  // Yasaklı içerikler / pornografi / istismar varyasyonları
  /\b(cp|csam|pedofili|porno|nsfw|эро|hentai|ifsa|ifşaa)\b/i,

  // Takas içerikleri (Madde, hesap, eşya veya illegal takas varyasyonları)
  /\b(takas|tks|satilik|satılık|kiralik|kiralık|fiyat|ucret|ücret|ibanda|papara|paparda)\s*(eder|olur|var|oyun|hesap|discord|tg)\b/i,

  // Dış grup / kanal / yönlendirme linkleri
  /(t\.me|telegram\.me|discord\.gg|whatsapp\.com|instagram\.com|linktr\.ee|bit\.ly)/i,
  /(@[a-zA-Z0-9_]{5,})/ // Telegram kullanıcı adı veya grup etiketleri
];

// Metni veya Profili Denetleyen Ana Fonksiyon
function checkContentSafety(textToCheck) {
  if (!textToCheck) return { safe: true, reason: null };

  const raw = String(textToCheck);
  const normalized = normalizeText(raw);

  // 1. Link ve Grup Yönlendirmesi Kontrolü
  if (/(t\.me|telegram\.me|discord\.gg|whatsapp\.com|linktr\.ee)/i.test(raw)) {
    return { safe: false, reason: 'Harici grup / kanal linki paylaşımı yasaktır.' };
  }

  // 2. Regex Desen Eşleşme Kontrolü (Kombinasyonlu tarama)
  for (const pattern of BANNED_PATTERNS) {
    if (pattern.test(raw) || pattern.test(normalized)) {
      return { safe: false, reason: 'Yasaklı içerik, yaş sınırı ihlali veya ticari/takas faaliyeti tespit edildi.' };
    }
  }

  return { safe: true, reason: null };
}

// Profil Adı (Nickname) ve Biyografi İncelemesi (Cümle şekilli nickler dahil)
function checkProfileSafety(profileData) {
  const fieldsToCheck = [
    profileData.nickname,
    profileData.bio,
    profileData.username,
    ...(profileData.photos || [])
  ];

  for (const field of fieldsToCheck) {
    if (field && typeof field === 'string') {
      const result = checkContentSafety(field);
      if (!result.safe) {
        return result;
      }
    }
  }

  return { safe: true, reason: null };
}

module.exports = {
  normalizeText,
  checkContentSafety,
  checkProfileSafety
};
