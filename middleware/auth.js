const crypto = require('crypto');
const { GroupConfig } = require('../models/schemas');

// Telegram initData HMAC-SHA256 Doğrulaması
function verifyTelegramInitData(req, res, next) {
  const initData = req.headers['x-telegram-init-data'];
  const botToken = process.env.GUARDIAN_BOT_TOKEN;

  if (!initData) {
    return res.status(401).json({ success: false, message: 'Kimlik doğrulama verisi eksik.' });
  }

  try {
    const urlParams = new URLSearchParams(initData);
    const hash = urlParams.get('hash');
    urlParams.delete('hash');

    const paramsList = Array.from(urlParams.entries()).sort(([a], [b]) => a.localeCompare(b));
    const dataCheckString = paramsList.map(([key, val]) => `${key}=${val}`).join('\n');

    const secretKey = crypto.createHmac('sha256', 'WebAppData').update(botToken);
    const calculatedHash = crypto.createHmac('sha256', secretKey.digest()).update(dataCheckString).digest('hex');

    if (calculatedHash === hash) {
      const userParam = urlParams.get('user');
      req.user = userParam ? JSON.parse(userParam) : null;
      return next();
    }
  } catch (e) {
    return res.status(403).json({ success: false, message: 'Geçersiz imza.' });
  }

  return res.status(403).json({ success: false, message: 'Yetkilendirme başarısız.' });
}

// Gruba Özel Yetki Kontrolü (RBAC Middleware)
function requireGroupPermission(permissionKey) {
  return async (req, res, next) => {
    try {
      const groupId = req.params.groupId || req.body.groupId;
      const userId = String(req.user.id);
      const founderId = String(process.env.FOUNDER_ID);

      // Kurucu her zaman tam yetkilidir (Founder Immunitiy)
      if (userId === founderId) return next();

      const group = await GroupConfig.findOne({ groupId });
      if (!group) {
        return res.status(404).json({ success: false, message: 'Grup bulunamadı.' });
      }

      const staff = group.staffPermissions.find(s => s.adminId === userId);
      if (!staff || !staff[permissionKey]) {
        return res.status(403).json({ success: false, message: `Bu işlem için [${permissionKey}] yetkiniz yok!` });
      }

      next();
    } catch (e) {
      res.status(500).json({ success: false, message: 'Yetki denetim hatası.' });
    }
  };
}

module.exports = { verifyTelegramInitData, requireGroupPermission };
