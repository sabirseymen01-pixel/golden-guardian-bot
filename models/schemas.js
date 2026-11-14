const mongoose = require('mongoose');

// Grup ve Granüler Yetki Yapısı
const GroupConfigSchema = new mongoose.Schema({
  groupId: { type: String, required: true, unique: true }, // Telegram Grup ID (-100...)
  groupName: { type: String, required: true },
  groupPhotoUrl: { type: String, default: '' },
  settings: {
    welcomeMessage: { type: String, default: "Aramıza hoş geldin! 🎉" },
    buttonText: { type: String, default: "Mini App'e Git" },
    buttonLink: { type: String, default: "https://t.me/GoldenMatchBot" },
    isLocked: { type: Boolean, default: false }
  },
  staffPermissions: [{
    adminId: String, // Admin Telegram ID
    adminUsername: String,
    canBan: { type: Boolean, default: false },
    canMute: { type: Boolean, default: false },
    canLock: { type: Boolean, default: false }
  }]
}, { timestamps: true });

// Şeffaf Adli Sicil ve Audit Log Şeması (Kim ne yaptıysa)
const SystemLogSchema = new mongoose.Schema({
  groupId: { type: String, required: true },
  adminId: { type: String, required: true },
  adminUsername: { type: String, required: true },
  action: { type: String, required: true }, // BAN, MUTE, LOCK, etc.
  targetUser: { type: String, default: 'N/A' },
  details: { type: String },
  timestamp: { type: Date, default: Date.now }
});

const GroupConfig = mongoose.model('GroupConfig', GroupConfigSchema);
const SystemLog = mongoose.model('SystemLog', SystemLogSchema);

module.exports = { GroupConfig, SystemLog };
