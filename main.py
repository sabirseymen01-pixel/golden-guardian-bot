<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Golden Katya | 3D Spatial HUD Cockpit</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <style>
        :root {
            --space-black: #020100;
            --hud-gold: #d4af37;
            --gold-glow: #f8ecbe;
            --danger-red: #ff3333;
            --glass-panel: rgba(10, 8, 4, 0.90);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: monospace; -webkit-tap-highlight-color: transparent; }
        
        html, body {
            height: 100vh; width: 100vw; overflow: hidden; background-color: var(--space-black);
        }

        body {
            background: radial-gradient(circle at 50% 50%, rgba(212, 175, 55, 0.2) 0%, rgba(2, 1, 0, 0.98) 85%);
            color: #fff; display: flex; flex-direction: column; justify-content: space-between;
        }

        /* 🌌 3D Uzay Kokpiti Tuvali (Three.js WebGL Viewport) */
        #webgl-container {
            position: absolute; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 1; pointer-events: none;
        }

        /* 🪞 Üst Komuta Çerçevesi (Holografik Header) */
        .hud-header {
            position: relative; z-index: 10; display: flex; justify-content: space-between; align-items: center;
            padding: 8px 12px; background: rgba(8, 6, 3, 0.85); backdrop-filter: blur(15px);
            border-bottom: 1px solid rgba(212,175,55,0.4); box-shadow: 0 5px 20px rgba(0,0,0,0.8);
        }
        .logo { font-size: 11px; color: var(--gold-glow); font-weight: 900; letter-spacing: 2px; text-shadow: 0 0 12px var(--hud-gold); }
        .badge { background: rgba(212,175,55,0.25); color: var(--gold-glow); padding: 4px 10px; border-radius: 8px; font-size: 9px; border: 1px solid var(--hud-gold); font-weight: 900; }

        /* 🪐 3D Yörünge ve Radar Paneli */
        .cockpit-viewport {
            position: relative; z-index: 10; flex: 1; display: flex; flex-direction: column; justify-content: space-between; padding: 8px;
        }

        .radar-deck {
            position: relative; width: 100%; height: 160px; display: flex; align-items: center; justify-content: center;
            background: rgba(4, 3, 1, 0.75); border-radius: 50%; border: 1.5px solid rgba(212,175,55,0.5);
            box-shadow: 0 0 40px rgba(212,175,55,0.2), inset 0 0 25px rgba(212,175,55,0.15);
            backdrop-filter: blur(10px); margin: 0 auto; max-width: 320px;
        }
        
        /* 🛡️ Merkez Katya Güvenlik Çekirdeği */
        .katya-core {
            position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
            width: 50px; height: 50px; border-radius: 50%; background: radial-gradient(circle, #f8ecbe 0%, #d4af37 60%, #3a2e0f 100%);
            border: 2px solid var(--gold-glow); display: flex; flex-direction: column; align-items: center; justify-content: center;
            box-shadow: 0 0 25px var(--hud-gold); z-index: 15; cursor: pointer;
        }
        .core-icon { font-size: 16px; }
        .core-text { font-size: 6px; font-weight: 900; color: #000; text-transform: uppercase; margin-top: 1px; }

        .orbit-ring {
            position: absolute; width: 100%; height: 100%; left: 0; top: 0; animation: orbitSpin 30s linear infinite;
        }
        @keyframes orbitSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        .orbit-node {
            position: absolute; left: 50%; top: 50%; display: flex; flex-direction: column; align-items: center; cursor: pointer; z-index: 12;
        }
        .orbit-node-inner { display: flex; flex-direction: column; align-items: center; animation: counterSpin 30s linear infinite; }
        @keyframes counterSpin { from { transform: rotate(0deg); } to { transform: rotate(-360deg); } }

        .orbit-avatar {
            width: 36px; height: 36px; border-radius: 50%; border: 2px solid var(--hud-gold);
            background: #000; overflow: hidden; display: flex; align-items: center; justify-content: center;
            box-shadow: 0 0 12px var(--hud-gold); transition: transform 0.3s ease;
        }
        .orbit-node:hover .orbit-avatar { transform: scale(1.2); border-color: #fff; box-shadow: 0 0 20px #fff; }
        .orbit-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .orbit-label { font-size: 7px; color: #d4c8b0; background: rgba(0,0,0,0.9); padding: 1px 4px; border-radius: 4px; margin-top: 2px; max-width: 60px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border: 1px solid rgba(212,175,55,0.3); }

        /* 🪞 Holografik Modül Sekmeleri (Cam Paneller) */
        .hud-nav { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-top: 6px; }
        .hud-tab {
            background: rgba(15, 11, 6, 0.9); backdrop-filter: blur(20px);
            border: 1px solid rgba(212, 175, 55, 0.45); color: #d4c8b0; padding: 8px 4px;
            font-size: 8px; font-weight: 900; border-radius: 8px; cursor: pointer; text-transform: uppercase; text-align: center;
            box-shadow: 0 5px 15px rgba(0,0,0,0.8), inset 0 0 10px rgba(212, 175, 55, 0.15); transition: all 0.3s ease;
        }
        .hud-tab.active {
            background: linear-gradient(135deg, rgba(212,175,55,0.6) 0%, rgba(170,119,28,0.5) 100%);
            color: var(--gold-glow); border-color: var(--gold-glow); box-shadow: 0 0 20px var(--hud-gold), inset 0 0 12px rgba(212,175,55,0.4);
        }

        /* 🎛️ Ana Holografik İçerik Ekranı */
        .hud-viewport {
            background: var(--glass-panel); backdrop-filter: blur(25px); border: 1.5px solid var(--hud-gold);
            border-radius: 12px; padding: 10px; overflow-y: auto; max-height: 230px; margin-top: 6px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.9), inset 0 0 20px rgba(212,175,55,0.12);
        }
        .mod-section { display: none; flex-direction: column; gap: 6px; }
        .mod-section.active { display: flex; }

        .section-title { font-size: 9px; font-weight: 900; color: var(--gold-glow); border-bottom: 1px solid rgba(212,175,55,0.3); padding-bottom: 3px; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 1px; }

        input, select, textarea {
            width: 100%; background: rgba(0,0,0,0.85); border: 1px solid rgba(212,175,55,0.4);
            border-radius: 6px; padding: 7px; color: #fff; font-size: 9px; outline: none; margin-bottom: 5px;
        }
        input:focus { border-color: var(--gold-glow); box-shadow: 0 0 8px var(--hud-gold); }

        .cyber-btn {
            width: 100%; background: linear-gradient(135deg, #d4af37 0%, #aa771c 100%); color: #000;
            border: none; padding: 8px; border-radius: 6px; font-weight: 900; font-size: 8px; cursor: pointer; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .cyber-btn:active { transform: scale(0.97); }

        .item-row { background: rgba(0,0,0,0.65); border: 1px solid rgba(212,175,55,0.25); padding: 6px; border-radius: 6px; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; font-size: 8px; }
        .log-box { background: rgba(0,0,0,0.75); border: 1px solid rgba(212,175,55,0.3); padding: 6px; border-radius: 6px; margin-bottom: 4px; font-size: 7px; color: #d4c8b0; }

        /* Katya AI Terminali */
        .katya-box { display: flex; flex-direction: column; height: 140px; background: rgba(0,0,0,0.95); border-radius: 6px; border: 1px solid var(--hud-gold); overflow: hidden; }
        .katya-screen { flex: 1; padding: 6px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; font-size: 8px; }
        .j-msg { max-width: 85%; padding: 5px 7px; border-radius: 5px; line-height: 1.2; }
        .j-msg.ai { background: rgba(212,175,55,0.18); color: var(--gold-glow); border: 1px solid rgba(212,175,55,0.45); align-self: flex-start; }
        .j-msg.user { background: rgba(255,255,255,0.1); color: #fff; align-self: flex-end; }
        .j-input-bar { display: flex; padding: 4px; background: #000; border-top: 1px solid var(--hud-gold); gap: 4px; }
        .j-input-bar input { margin-bottom: 0; }
    </style>
</head>
<body>

    <!-- 🌌 Arka Plan 3D Uzay Kokpiti Simülasyonu -->
    <div id="webgl-container"></div>

    <!-- Üst Komuta Çerçevesi -->
    <div class="hud-header">
        <div class="logo">⚜️ GOLDEN KATYA 3D HUD</div>
        <div class="badge" id="userRole">Kraljica 👑</div>
    </div>

    <!-- Kokpit Arayüz Alanı -->
    <div class="cockpit-viewport">
        <!-- 🪐 3D Yörünge Radar Halkası -->
        <div class="radar-deck">
            <div class="katya-core" onclick="selectGroupNode(0)" title="Genel Hub">
                <div class="core-icon">⚜️</div>
                <div class="core-text">Katya</div>
            </div>
            <div class="orbit-ring" id="radarNodesRing">
                <!-- Dinamik Dönen Düğümler -->
            </div>
        </div>

        <!-- 🪞 Dokunmatik Şeffaf Cam Sekmeler -->
        <div class="hud-nav">
            <div class="hud-tab active" onclick="switchMod('telemetry', event)">Telemetri</div>
            <div class="hud-tab" onclick="switchMod('staff', event)">Staff Matrisi</div>
            <div class="hud-tab" onclick="switchMod('tactical', event)">Taktiksel</div>
            <div class="hud-tab" onclick="switchMod('logs', event)">Loglar</div>
            <div class="hud-tab" onclick="switchMod('settings', event)">Protokoller</div>
            <div class="hud-tab" onclick="switchMod('jarvis', event)">🤖 Katya AI</div>
        </div>

        <!-- 🎛️ Holografik İçerik Paneli -->
        <div class="hud-viewport">
            <!-- TELEMETRİ -->
            <div id="mod-telemetry" class="mod-section active">
                <div class="section-title">📊 Komuta Telemetrisi</div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px;">
                    <div style="background: rgba(0,0,0,0.65); padding: 8px; border-radius: 6px; border: 1px solid rgba(212,175,55,0.35);">
                        <div style="font-size: 7px; color: #d4c8b0;">TOPLAM İŞLEM</div>
                        <div id="totalActions" style="font-size: 13px; font-weight: 900; color: var(--gold-glow);">-</div>
                    </div>
                    <div style="background: rgba(0,0,0,0.65); padding: 8px; border-radius: 6px; border: 1px solid rgba(212,175,55,0.35);">
                        <div style="font-size: 7px; color: #d4c8b0;">OTONOM BLOKE</div>
                        <div id="totalBans" style="font-size: 13px; font-weight: 900; color: var(--danger-red);">-</div>
                    </div>
                </div>
            </div>

            <!-- STAFF -->
            <div id="mod-staff" class="mod-section">
                <div class="section-title">👑 Yetki Matrisi</div>
                <input type="text" id="staffInput" placeholder="@kullaniciadi veya ID">
                <input type="text" id="staffName" placeholder="Rozet Ünvanı (Örn: 💎 Kristal)">
                <button class="cyber-btn" onclick="addStaff()">Mühürle</button>
                <div id="staffListContainer" style="max-height: 90px; overflow-y: auto; margin-top: 4px;"><div style="font-size:8px;">Yükleniyor...</div></div>
            </div>

            <!-- TAKTİKSEL -->
            <div id="mod-tactical" class="mod-section">
                <div class="section-title">⚡ Taktiksel Müdahale</div>
                <input type="text" id="memberTargetInput" placeholder="Kullanıcı ID veya @username">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px;">
                    <button class="cyber-btn" style="background: var(--danger-red); color:#fff;" onclick="memberAction('ban')">Banla 🚫</button>
                    <button class="cyber-btn" style="background: #2ecc71; color:#fff;" onclick="memberAction('unban')">Ban Aç ✅</button>
                    <button class="cyber-btn" style="background: #e67e22; color:#fff;" onclick="memberAction('mute')">Mute 🔇</button>
                    <button class="cyber-btn" style="background: #3498db; color:#fff;" onclick="memberAction('unmute')">Unmute 🔊</button>
                </div>
            </div>

            <!-- LOGLAR -->
            <div id="mod-logs" class="mod-section">
                <div class="section-title">📜 Zaman Akış Logları</div>
                <div style="display: flex; gap: 5px; margin-bottom: 5px;">
                    <select id="logClearDays" style="margin-bottom:0;"><option value="1">Günlük</option><option value="7">Haftalık</option><option value="0">Tümünü Sil</option></select>
                    <button class="cyber-btn" style="width: auto; background: rgba(255,51,51,0.3); color: var(--danger-red); border: 1px solid var(--danger-red);" onclick="clearLogs()">Temizle</button>
                </div>
                <div id="generalLogsContainer" style="max-height: 100px; overflow-y: auto;"><div style="font-size:8px;">Loglar akıyor...</div></div>
            </div>

            <!-- PROTOKOLLER -->
            <div id="mod-settings" class="mod-section">
                <div class="section-title">⚙️ Protokoller</div>
                <textarea id="welcomeMsgInput" rows="2" placeholder="Karşılama metni..."></textarea>
                <input type="text" id="btnTextInput" placeholder="Buton Metni">
                <input type="text" id="btnUrlInput" placeholder="Buton Linki (https://)">
                <button class="cyber-btn" onclick="saveSettings()">Kaydet</button>
            </div>

            <!-- KATYA AI -->
            <div id="mod-jarvis" class="mod-section" style="height: 100%;">
                <div class="katya-box">
                    <div class="katya-screen" id="jarvisScreen">
                        <div class="j-msg ai">⚜️ Katya 3D kokpit motoru aktif, Kraljica. Komut bekliyorum.</div>
                    </div>
                    <div class="j-input-bar">
                        <input type="text" id="jarvisInput" placeholder="Katya'ya komut ver..." onkeypress="handleJarvisKey(event)">
                        <button class="cyber-btn" style="width: 50px; padding: 0; font-size: 7px;" onclick="sendJarvisCommand()">İlet</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 🌌 THREE.JS 3D UZAY KOKPİT ARKA PLAN MOTORU
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setSize(window.innerWidth, window.innerHeight);
        document.getElementById('webgl-container').appendChild(renderer.domElement);

        // Altın Tozu / Yıldız Alanı Parçacıkları (Starfield)
        const particlesCount = 400;
        const geometry = new THREE.BufferGeometry();
        const positions = new Float32Array(particlesCount * 3);

        for (let i = 0; i < particlesCount * 3; i += 3) {
            positions[i] = (Math.random() - 0.5) * 15;
            positions[i + 1] = (Math.random() - 0.5) * 15;
            positions[i + 2] = (Math.random() - 0.5) * 15;
        }
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        const material = new THREE.PointsMaterial({ color: 0xd4af37, size: 0.04, transparent: true, opacity: 0.8 });
        const starField = new THREE.Points(geometry, material);
        scene.add(starField);

        camera.position.z = 5;

        function animate3D() {
            requestAnimationFrame(animate3D);
            starField.rotation.y += 0.001;
            starField.rotation.x += 0.0005;
            renderer.render(scene, camera);
        }
        animate3D();

        window.addEventListener('resize', () => {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        });

        // TELEGRAM WEB APP & KONTROL MİMARİSİ
        const tg = window.Telegram.WebApp;
        try { tg.ready(); tg.expand(); } catch(e) {}
        
        let user = tg.initDataUnsafe?.user || { id: 8245373459, first_name: "Breskavica", username: "breskavica" };
        let selectedChatId = 0;

        function switchMod(modName, evt) {
            document.querySelectorAll('.mod-section').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.hud-tab').forEach(el => el.classList.remove('active'));
            document.getElementById('mod-' + modName).classList.add('active');
            if (evt && evt.currentTarget) evt.currentTarget.classList.add('active');
        }

        async function verifyAccess() {
            try {
                const res = await fetch('/api/verify-admin', { 
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify({ user_id: user.id }) 
                });
                const data = await res.json();
                if (!data.authorized) { document.body.innerHTML = `<div style="text-align:center; padding:50px; color:red;"><h2>⛔ YETKİSİZ ERİŞİM!</h2></div>`; return; }
                const isFounder = String(user.id) === "8245373459" || data.is_founder;
                document.getElementById('userRole').innerText = (user.first_name || 'Admin') + (isFounder ? " Kraljica 👑" : " 🛡️");
                await loadGroups(); 
                await loadData(); 
                setInterval(loadData, 5000);
            } catch (e) {}
        }

        async function loadGroups() {
            try {
                const res = await fetch('/api/groups');
                const groups = await res.json();
                const ringContainer = document.getElementById('radarNodesRing');

                if (ringContainer) {
                    let html = '';
                    const radius = 62; 

                    groups.forEach((g, index) => {
                        const angle = (index / groups.length) * 2 * Math.PI;
                        const x = Math.cos(angle) * radius;
                        const y = Math.sin(angle) * radius;
                        const isActive = selectedChatId == g.chat_id;

                        let avatar = `<span style="font-size:14px;">🛡️</span>`;
                        if (g.photo_url) {
                            avatar = `<img src="${g.photo_url}" onerror="this.parentElement.innerHTML='🛡️'">`;
                        }

                        html += `
                            <div class="orbit-node ${isActive ? 'active' : ''}" 
                                 style="transform: translate(-50%, -50%) translate(${x}px, ${y}px);" 
                                 onclick="selectGroupNode(${g.chat_id})">
                                <div class="orbit-node-inner">
                                    <div class="orbit-avatar">${avatar}</div>
                                    <div class="orbit-label">${g.title || 'Grup'}</div>
                                </div>
                            </div>
                        `;
                    });
                    ringContainer.innerHTML = html;
                }
            } catch (e) {}
        }

        function selectGroupNode(chatId) {
            selectedChatId = chatId;
            loadGroups();
            loadData();
        }

        async function loadData() {
            try {
                const statsRes = await fetch('/api/stats');
                const stats = await statsRes.json();
                document.getElementById('totalActions').innerText = stats.total_actions || 0;
                const bd = stats.action_breakdown || {};
                document.getElementById('totalBans').innerText = (bd['BAN'] || 0) + (bd['AUTO_BAN'] || 0);

                const genRes = await fetch('/api/logs?filter=general&limit=30');
                const genLogs = await genRes.json();
                const genC = document.getElementById('generalLogsContainer');
                genC.innerHTML = genLogs.length === 0 ? '<div style="font-size:8px; color:#d4c8b0;">Log akışı boş.</div>' : '';
                genLogs.forEach(l => {
                    genC.innerHTML += `
                        <div class="log-box">
                            <div style="display:flex; justify-content:space-between;">
                                <span style="color:var(--danger-red); font-weight:900;">${l.action_type}</span>
                                <span style="font-size:6px;">${l.timestamp}</span>
                            </div>
                            <div>Hedef: <span style="color:var(--gold-glow);">@${l.username || l.target_id}</span></div>
                        </div>
                    `;
                });

                const staffRes = await fetch('/api/staff/list', { 
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify({ user_id: user.id, chat_id: selectedChatId }) 
                });
                const staffs = await staffRes.json();
                const staffC = document.getElementById('staffListContainer');
                staffC.innerHTML = staffs.length === 0 ? '<div style="font-size:8px; color:#d4c8b0;">Bu yörüngede yetkili yok.</div>' : '';
                
                staffs.forEach(s => {
                    const sName = s.full_name || s.name || s.first_name || "Muhafız";
                    const sUser = s.username ? `@${s.username}` : (s.user_id ? `ID: ${s.user_id}` : "Kayıtlı Yetkili");
                    const targetId = s.user_id || s.id || 0;

                    staffC.innerHTML += `
                        <div class="item-row">
                            <div><b>${sName}</b><br><span style="font-size:6px; color:#d4c8b0;">${sUser}</span></div>
                            <button onclick="removeStaff(${targetId})" style="background:#444; color:#fff; border:none; padding:2px 5px; border-radius:3px; font-size:6px; cursor:pointer;">Sil</button>
                        </div>
                    `;
                });
            } catch (e) {}
        }

        async function addStaff() {
            const input = document.getElementById('staffInput').value.trim();
            const name = document.getElementById('staffName').value.trim() || '👑 Muhafız';
            if (!input) return alert("Değer girin!");
            await fetch('/api/staff/add', { 
                method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify({ user_id: user.id, chat_id: selectedChatId, staff_input: input, full_name: name }) 
            });
            document.getElementById('staffInput').value = ''; 
            document.getElementById('staffName').value = ''; 
            loadData(); 
            alert("✅ Yetkili mühürlendi!");
        }

        async function removeStaff(targetId) {
            if (!confirm("Silinsin mi?")) return;
            await fetch('/api/staff/remove', { 
                method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify({ user_id: user.id, chat_id: selectedChatId, target_id: targetId }) 
            });
            loadData();
        }

        async function memberAction(actionType) {
            const target = document.getElementById('memberTargetInput').value.trim();
            if (!target) return alert("Kullanıcı ID veya @username girin!");
            if (!selectedChatId) return alert("Lütfen yörüngeden bir grup seçin!");
            const res = await fetch('/api/member/action', { 
                method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify({ user_id: user.id, target: target, action: actionType, chat_id: selectedChatId }) 
            });
            const data = await res.json();
            if (data.success) { alert(`✅ İşlem tamamlandı`); document.getElementById('memberTargetInput').value = ''; loadData(); } 
            else { alert("❌ Hata: " + (data.error || 'Yetkisiz!')); }
        }

        async function clearLogs() {
            if (!confirm("Temizlensin mi?")) return;
            await fetch('/api/logs/clear', { 
                method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify({ user_id: user.id, days: parseInt(document.getElementById('logClearDays').value) }) 
            });
            loadData(); 
        }

        async function saveSettings() {
            const welcome = document.getElementById('welcomeMsgInput').value.trim();
            const btnText = document.getElementById('btnTextInput').value.trim();
            const btnUrl = document.getElementById('btnUrlInput').value.trim();
            if (!selectedChatId) return alert("Grup seçin!");
            await fetch('/api/settings/update', { 
                method: 'POST', headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify({ user_id: user.id, chat_id: selectedChatId, welcome_msg: welcome, btn_text: btnText, btn_url: btnUrl }) 
            });
            alert("⚜️ Kaydedildi!");
        }

        function handleJarvisKey(e) { if (e.key === 'Enter') sendJarvisCommand(); }

        function sendJarvisCommand() {
            const input = document.getElementById('jarvisInput');
            const text = input.value.trim();
            if (!text) return;
            const screen = document.getElementById('jarvisScreen');
            screen.innerHTML += `<div class="j-msg user">${text}</div>`;
            input.value = '';
            screen.scrollTop = screen.scrollHeight;
            setTimeout(() => {
                let reply = "⚜️ Katya 3D kalkanları kararlı, Kraljica.";
                if (text.toLowerCase().includes('rapor')) reply = "📊 Katya Uzay Taraması: Tüm yörüngeler temiz, savunma hatları tam güçte.";
                screen.innerHTML += `<div class="j-msg ai">${reply}</div>`;
                screen.scrollTop = screen.scrollHeight;
            }, 400);
        }

        verifyAccess();
    </script>
</body>
</html>
