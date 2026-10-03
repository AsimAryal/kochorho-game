/* KoChorHo client */
(() => {
    'use strict';

    // --- Helpers -----------------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const show = (el, visible = true) => (typeof el === 'string' ? $(el) : el).classList.toggle('hidden', !visible);

    const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

    const safeParse = (text) => {
        try { return JSON.parse(text); } catch { return null; }
    };

    const wsUrl = (path) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`;

    function hue(str) {
        let h = 0;
        for (const ch of String(str)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
        return 200 + (h % 160); // blue → purple → pink → coral, to sit nicely with the theme
    }

    function initials(name) {
        const parts = String(name || '?').trim().split(/\s+/);
        const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2);
        return letters.toUpperCase();
    }

    function avatarHtml(id, name, size = '') {
        return `<span class="avatar ${size}" style="--h:${hue(id)}">${esc(initials(name))}</span>`;
    }

    function toast(message, type = 'info', ms = 3200) {
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        const icon = type === 'error' ? '⚠️' : type === 'success' ? '✅' : '💬';
        el.innerHTML = `<span aria-hidden="true">${icon}</span><span>${esc(message)}</span>`;
        $('toast-stack').appendChild(el);
        setTimeout(() => {
            el.classList.add('leaving');
            el.addEventListener('animationend', () => el.remove(), { once: true });
        }, ms);
    }

    // --- Persistent identity -----------------------------------------------
    const STORAGE = { name: 'kochorho_nickname', id: 'kochorho_id', room: 'kochorho_room' };

    let nickname = localStorage.getItem(STORAGE.name) || '';
    let clientId = localStorage.getItem(STORAGE.id);
    if (!clientId) {
        // crypto.randomUUID is only available on https/localhost, so fall back on plain LAN http
        const rand = (window.crypto && crypto.randomUUID)
            ? crypto.randomUUID().replace(/-/g, '').slice(0, 12)
            : Math.random().toString(36).slice(2, 11) + Date.now().toString(36).slice(-3);
        clientId = 'user_' + rand;
        localStorage.setItem(STORAGE.id, clientId);
    }

    // --- Connection status pill --------------------------------------------
    function setConnecting(isReconnecting) {
        show('conn-status', isReconnecting);
    }

    // --- Views -------------------------------------------------------------
    const VIEWS = ['onboarding', 'lobby', 'game'];
    let currentView = null;

    function showView(name) {
        currentView = name;
        VIEWS.forEach((v) => show(`view-${v}`, v === name));
        updateProfileChip();
        window.scrollTo({ top: 0 });
    }

    function updateProfileChip() {
        const chip = $('profile-chip');
        show(chip, Boolean(nickname) && currentView !== 'onboarding');
        $('profile-name').textContent = nickname;
        const av = $('profile-avatar');
        av.textContent = initials(nickname);
        av.style.setProperty('--h', hue(clientId));
        chip.disabled = currentView === 'game';
        chip.title = currentView === 'game' ? nickname : 'Change your name';
    }

    function showOnboarding() {
        disconnectLobbyWs();
        $('nickname-input').value = nickname;
        showView('onboarding');
        setTimeout(() => $('nickname-input').focus(), 50);
    }

    function showLobby() {
        showView('lobby');
        connectLobbyWs();
    }

    // --- Onboarding --------------------------------------------------------
    $('profile-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const value = $('nickname-input').value.replace(/\s+/g, ' ').trim();
        if (!value) {
            toast('Please enter a nickname first.', 'error');
            $('nickname-input').focus();
            return;
        }
        nickname = value;
        localStorage.setItem(STORAGE.name, nickname);
        showLobby();
    });

    $('profile-chip').addEventListener('click', () => {
        if (currentView !== 'game') showOnboarding();
    });

    // --- Lobby tabs --------------------------------------------------------
    function switchTab(tab) {
        ['join', 'host'].forEach((t) => {
            const btn = $(`tab-${t}`);
            btn.classList.toggle('active', t === tab);
            btn.setAttribute('aria-selected', String(t === tab));
            show(`content-${t}`, t === tab);
        });
        document.querySelector('.segmented').dataset.active = tab;
    }
    $('tab-join').addEventListener('click', () => switchTab('join'));
    $('tab-host').addEventListener('click', () => switchTab('host'));

    // --- Lobby WebSocket ---------------------------------------------------
    let lobbyWs = null;
    let lobbyRetry = 0;
    let lobbyRetryTimer = null;
    let wantLobby = false;
    let pendingCreate = false;

    function connectLobbyWs() {
        wantLobby = true;
        clearTimeout(lobbyRetryTimer);
        if (lobbyWs && lobbyWs.readyState <= WebSocket.OPEN) return; // CONNECTING or OPEN

        const ws = new WebSocket(wsUrl('/ws/lobby'));
        lobbyWs = ws;

        ws.onopen = () => {
            lobbyRetry = 0;
            setConnecting(false);
            if (pendingCreate) {
                pendingCreate = false;
                createGame();
            }
        };

        ws.onmessage = (event) => {
            const data = safeParse(event.data);
            if (!data) return;
            if (data.type === 'gamelist') {
                renderGameList(data.games || []);
            } else if (data.type === 'game_created') {
                enterGame(data.room_code);
            } else if (data.type === 'error') {
                toast(data.message, 'error');
            }
        };

        ws.onclose = () => {
            if (lobbyWs !== ws) return; // Superseded or intentionally closed
            lobbyWs = null;
            if (!wantLobby) return;
            setConnecting(true);
            const delay = Math.min(8000, 500 * 2 ** lobbyRetry++);
            lobbyRetryTimer = setTimeout(connectLobbyWs, delay);
        };
    }

    function disconnectLobbyWs() {
        wantLobby = false;
        clearTimeout(lobbyRetryTimer);
        const ws = lobbyWs;
        lobbyWs = null;
        if (ws) ws.close(1000);
        setConnecting(false);
    }

    function createGame() {
        const btn = $('create-game-btn');
        if (!lobbyWs || lobbyWs.readyState !== WebSocket.OPEN) {
            pendingCreate = true;
            connectLobbyWs();
            return;
        }
        btn.disabled = true;
        lobbyWs.send(JSON.stringify({ type: 'create_game' }));
        setTimeout(() => { btn.disabled = false; }, 1500);
    }
    $('create-game-btn').addEventListener('click', createGame);

    function renderGameList(games) {
        const list = $('game-list');
        if (!games.length) {
            list.innerHTML = `
                <div class="empty-state">
                    <span class="empty-icon" aria-hidden="true">🛋️</span>
                    No open rooms right now.<br><small>Host one, or enter a code below.</small>
                </div>`;
            return;
        }
        list.innerHTML = games.map((g, i) => `
            <div class="game-item" style="animation-delay:${i * 40}ms">
                ${avatarHtml(g.host, g.host)}
                <div class="game-info">
                    <strong>${esc(g.host)}'s room</strong>
                    <small><span class="game-code">${esc(g.code)}</span> · ${g.count} ${g.count === 1 ? 'player' : 'players'}${g.mystery_mode ? ' · Mystery' : ''}</small>
                </div>
                <button type="button" class="btn btn-secondary btn-sm" data-join="${esc(g.code)}">Join</button>
            </div>`).join('');
    }

    $('game-list').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-join]');
        if (btn) enterGame(btn.dataset.join);
    });

    $('join-code-input').addEventListener('input', (e) => {
        e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, '');
    });

    $('join-code-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const code = $('join-code-input').value.trim().toUpperCase();
        if (code.length !== 4) {
            toast('Room codes are 4 letters.', 'error');
            return;
        }
        $('join-code-input').value = '';
        enterGame(code);
    });

    // --- Game WebSocket ----------------------------------------------------
    let gameWs = null;
    let gameRetry = 0;
    let gameRetryTimer = null;
    let roomCode = '';
    let lastState = null;
    let revealKey = null; // Which round's word the flip-card state belongs to

    function enterGame(code) {
        roomCode = String(code).toUpperCase();
        localStorage.setItem(STORAGE.room, roomCode);
        disconnectLobbyWs();
        lastState = null;
        revealKey = null;
        $('display-room-code').textContent = roomCode;
        $('display-my-role').textContent = 'Connecting…';
        $('display-my-role').className = 'badge badge-muted';
        ['state-lobby-waiting', 'state-round-reveal', 'state-voting', 'state-results', 'spectator-panel']
            .forEach((id) => show(id, false));
        showView('game');
        connectGameWs();
    }

    function connectGameWs() {
        if (!roomCode) return;
        clearTimeout(gameRetryTimer);
        if (gameWs && gameWs.readyState <= WebSocket.OPEN) return;

        const ws = new WebSocket(wsUrl(`/ws/game/${encodeURIComponent(roomCode)}/${encodeURIComponent(clientId)}`));
        gameWs = ws;

        ws.onopen = () => {
            gameRetry = 0;
            setConnecting(false);
            ws.send(JSON.stringify({ action: 'join', nickname }));
        };

        ws.onmessage = (event) => {
            const data = safeParse(event.data);
            if (!data) return;
            if (data.type === 'error') {
                toast(data.message, 'error');
                return;
            }
            renderGameState(data);
        };

        ws.onclose = (event) => {
            if (gameWs !== ws) return; // Superseded or intentionally closed
            gameWs = null;

            if (event.code === 4000 || event.code === 4001) {
                // Game not found / already in progress: don't keep retrying
                toast(event.reason || 'That game is no longer available.', 'error');
                exitGame();
                return;
            }
            if (event.code === 4002) {
                // Same player opened the game somewhere else
                toast('This game is open in another tab.', 'info');
                roomCode = '';
                showLobby();
                return;
            }
            if (!roomCode) return;

            setConnecting(true);
            const delay = Math.min(8000, 500 * 2 ** gameRetry++);
            gameRetryTimer = setTimeout(connectGameWs, delay);
        };
    }

    function exitGame() {
        clearTimeout(gameRetryTimer);
        const ws = gameWs;
        gameWs = null;
        roomCode = '';
        lastState = null;
        localStorage.removeItem(STORAGE.room);
        if (ws) ws.close(1000);
        setConnecting(false);
        showLobby();
    }

    function sendGameAction(action, payload = {}) {
        if (!gameWs || gameWs.readyState !== WebSocket.OPEN) {
            toast('Reconnecting… try again in a moment.', 'error');
            return;
        }
        gameWs.send(JSON.stringify({ action, ...payload }));
    }

    // Phones drop sockets when the screen locks; reconnect immediately when we're visible again.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        if (roomCode && !gameWs) {
            gameRetry = 0;
            connectGameWs();
        } else if (wantLobby && !lobbyWs) {
            lobbyRetry = 0;
            connectLobbyWs();
        }
    });

    // --- Game controls -----------------------------------------------------
    $('leave-btn').addEventListener('click', () => {
        const active = lastState && ['ROUND_START', 'VOTING', 'RESULTS'].includes(lastState.state);
        if (active && !confirm('Leave this game? You can rejoin with the room code while it lasts.')) return;
        exitGame();
    });

    $('display-room-code').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(roomCode);
            toast(`Copied room code ${roomCode}`, 'success', 2000);
        } catch {
            toast(`Room code: ${roomCode}`, 'info', 2500); // Clipboard needs https on most phones
        }
    });

    $('mystery-mode-toggle').addEventListener('change', (e) => {
        sendGameAction('set_mystery_mode', { enabled: e.target.checked });
    });
    $('start-game-btn').addEventListener('click', () => sendGameAction('start_game'));
    $('start-voting-btn').addEventListener('click', () => sendGameAction('start_voting'));
    $('force-resolve-btn').addEventListener('click', () => {
        if (confirm('Close voting now and count the votes cast so far?')) sendGameAction('force_resolve');
    });
    $('next-round-btn').addEventListener('click', () => sendGameAction('next_round'));
    $('play-again-btn').addEventListener('click', () => sendGameAction('return_to_lobby'));
    $('reveal-imposter-btn').addEventListener('click', () => sendGameAction('reveal_imposter'));

    $('word-card').addEventListener('click', () => {
        const card = $('word-card');
        const revealed = !card.classList.contains('is-revealed');
        card.classList.toggle('is-revealed', revealed);
        card.setAttribute('aria-pressed', String(revealed));
    });

    $('voting-buttons').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-vote]');
        if (!btn || btn.disabled) return;
        // Optimistic highlight; the server confirms via the next state update
        document.querySelectorAll('#voting-buttons .vote-card').forEach((c) => c.classList.toggle('is-selected', c === btn));
        sendGameAction('vote', { target_id: btn.dataset.vote });
    });

    // --- Rendering ---------------------------------------------------------
    const PHASES = ['LOBBY', 'ROUND_START', 'VOTING', 'RESULTS'];

    function setPhase(state) {
        const phase = state === 'GAME_OVER' ? 'RESULTS' : state;
        const idx = PHASES.indexOf(phase);
        document.querySelectorAll('#phase-steps li').forEach((li, i) => {
            li.classList.toggle('done', i < idx || state === 'GAME_OVER');
            li.classList.toggle('current', i === idx && state !== 'GAME_OVER');
        });
    }

    function setRoleBadge(state, me) {
        const el = $('display-my-role');
        let text;
        let cls = 'badge';
        if (state.state === 'LOBBY') {
            text = me.is_host ? '👑 Host' : 'Player';
            cls += me.is_host ? ' badge-host' : ' badge-muted';
        } else if (state.my_role === 'Imposter') {
            text = '🎭 Imposter';
            cls += ' badge-imposter';
        } else if (state.my_role === 'Civilian') {
            text = '🙂 Civilian';
            cls += ' badge-civilian';
        } else if (state.my_role === '???') {
            text = '❓ Unknown';
            cls += ' badge-mystery';
        } else if (state.my_role === 'Spectator') {
            text = '👻 Spectator';
            cls += ' badge-muted';
        } else {
            text = 'Player';
            cls += ' badge-muted';
        }
        el.textContent = text;
        el.className = cls;
        show('display-mode-badge', state.mystery_mode);
    }

    function renderGameState(state) {
        const me = (state.players || []).find((p) => p.client_id === clientId);
        if (!me) return; // Wait for join confirmation
        const prev = lastState;
        lastState = state;

        // Let the host know when they've inherited the role
        if (prev && !prev.players.find((p) => p.client_id === clientId)?.is_host && me.is_host) {
            toast("You're now the host.", 'info');
        }

        setPhase(state.state);
        setRoleBadge(state, me);

        ['state-lobby-waiting', 'state-round-reveal', 'state-voting', 'state-results']
            .forEach((id) => show(id, false));

        switch (state.state) {
            case 'LOBBY': renderLobbyWaiting(state, me); break;
            case 'ROUND_START': renderRoundReveal(state, me); break;
            case 'VOTING': renderVoting(state, me); break;
            case 'RESULTS':
            case 'GAME_OVER': renderResults(state, me); break;
        }

        renderSpectatorPanel(state, me);
    }

    function renderLobbyWaiting(state, me) {
        show('state-lobby-waiting');

        const online = state.players.filter((p) => p.is_connected);
        $('player-count').textContent = online.length;
        $('player-list-waiting').innerHTML = state.players.map((p) => `
            <li class="player-chip ${p.client_id === clientId ? 'is-me' : ''} ${p.is_connected ? '' : 'is-offline'}">
                ${avatarHtml(p.client_id, p.nickname)}
                <span class="name">${esc(p.nickname)}</span>
                ${p.is_host ? '<span class="crown" title="Host">👑</span>' : ''}
                ${p.client_id === clientId && !p.is_host ? '<span class="tag">you</span>' : ''}
            </li>`).join('');

        show('host-controls', me.is_host);
        show('waiting-msg', !me.is_host);

        if (me.is_host) {
            $('mystery-mode-toggle').checked = state.mystery_mode;
            const min = state.min_players || 3;
            const missing = Math.max(0, min - online.length);
            const startBtn = $('start-game-btn');
            startBtn.disabled = missing > 0;
            startBtn.classList.toggle('is-ready', missing === 0);
            $('start-hint').textContent = missing > 0
                ? `Waiting for ${missing} more ${missing === 1 ? 'player' : 'players'} (minimum ${min}). Share the code ${state.room_code}!`
                : '';
        } else {
            $('guest-mode-label').textContent = state.mystery_mode ? 'Mystery' : 'Classic';
        }
    }

    function renderRoundReveal(state, me) {
        show('state-round-reveal');
        $('round-number').textContent = state.round_number || 1;

        const alive = me.is_alive;
        show('word-card', alive);
        show('role-pill', alive);
        $('reveal-instructions').textContent = alive
            ? 'Take turns describing your word without giving it away.'
            : 'Listen in and see if you can spot the imposter.';

        if (alive) {
            const role = state.my_role === '???' ? 'a mystery' : state.my_role === 'Imposter' ? 'the Imposter' : 'a Civilian';
            $('role-text').textContent = role;
            $('role-pill').className = 'badge ' + (state.my_role === 'Imposter' ? 'badge-imposter'
                : state.my_role === 'Civilian' ? 'badge-civilian' : 'badge-mystery');

            // Only flip the card back face-down when a new word arrives (not on every update)
            const key = `${state.round_number}:${state.my_word}`;
            if (key !== revealKey) {
                revealKey = key;
                $('secret-word').textContent = state.my_word || '';
                $('word-card').classList.remove('is-revealed');
                $('word-card').setAttribute('aria-pressed', 'false');
            }
        }

        show('start-voting-btn', me.is_host);
        show('reveal-wait', !me.is_host);
    }

    function renderVoting(state, me) {
        show('state-voting');
        const grid = $('voting-buttons');
        const myVote = state.my_vote;

        const candidates = state.players.filter((p) => p.is_alive);
        if (!me.is_alive) {
            $('voting-instructions').textContent = 'The living are voting. Sit tight.';
        } else {
            $('voting-instructions').textContent = myVote
                ? 'Vote locked in. You can still change it until everyone has voted.'
                : 'Tap a player to cast your vote. You can change it until everyone has voted.';
        }

        grid.innerHTML = candidates.map((p, i) => {
            const isMe = p.client_id === clientId;
            const disabled = isMe || !me.is_alive;
            const sub = isMe ? 'You' : !p.is_connected ? 'Offline' : p.has_voted ? 'Voted' : 'Thinking…';
            return `
                <button type="button" class="vote-card ${myVote === p.client_id ? 'is-selected' : ''} ${isMe ? 'is-me' : ''}"
                        data-vote="${esc(p.client_id)}" ${disabled ? 'disabled' : ''} style="animation-delay:${i * 35}ms">
                    ${p.has_voted ? '<span class="voted-dot" title="Has voted"></span>' : ''}
                    ${avatarHtml(p.client_id, p.nickname, 'avatar-lg')}
                    <span class="name">${esc(p.nickname)}</span>
                    <span class="sub">${sub}</span>
                </button>`;
        }).join('');

        const cast = state.votes_cast || 0;
        const needed = state.votes_needed || 0;
        $('vote-progress-bar').style.width = needed ? `${(cast / needed) * 100}%` : '0%';
        $('vote-progress-text').textContent = `${cast} / ${needed} voted`;

        show('force-resolve-btn', me.is_host && cast > 0 && cast < needed);
    }

    function renderResults(state, me) {
        show('state-results');
        const over = state.state === 'GAME_OVER';
        const result = state.last_result || { tally: [], tie: true };

        // Winner banner
        const banner = $('winner-banner');
        show(banner, over);
        if (over) {
            const civiliansWin = state.winner === 'Civilians';
            banner.className = `winner-banner ${civiliansWin ? 'civilians' : 'imposter'}`;
            const iWon = (state.my_role === 'Imposter') !== civiliansWin;
            $('winner-emoji').textContent = civiliansWin ? '🎉' : '🎭';
            $('result-winner-text').textContent = civiliansWin ? 'Civilians win!' : 'The imposter wins!';
            const name = state.imposter_identity || 'Unknown';
            $('winner-sub').textContent = (civiliansWin
                ? `${name} was the imposter.`
                : `${name} fooled everyone.`) + (iWon ? ' Nice one!' : '');
        }

        // Round outcome
        let icon;
        let title;
        let sub;
        if (result.tie) {
            icon = '🤝';
            title = result.tally.length ? "It's a tie!" : 'No votes were cast';
            sub = 'Nobody was eliminated this round.';
        } else {
            const eliminatedIsImposter = over && result.eliminated_id === state.imposter_id;
            icon = eliminatedIsImposter ? '🎯' : '🗳️';
            title = `${result.eliminated_name} was voted out`;
            sub = eliminatedIsImposter ? 'And they were the imposter!' : over ? 'They were innocent.' : 'They were not the imposter… the game goes on.';
        }
        $('round-outcome').innerHTML = `
            <span class="outcome-icon" aria-hidden="true">${icon}</span>
            <div><strong>${esc(title)}</strong><small>${esc(sub)}</small></div>`;

        // Words (game over only)
        show('words-reveal', over && Boolean(state.words));
        if (over && state.words) {
            $('word-civilian').textContent = state.words.civilian;
            $('word-imposter').textContent = state.words.imposter;
        }

        // Vote breakdown
        const votesById = Object.fromEntries((result.tally || []).map((r) => [r.client_id, r.votes]));
        const maxVotes = Math.max(1, ...Object.values(votesById));
        const knownImposter = (over || state.has_revealed) ? state.imposter_id : null;
        const rows = [...state.players].sort((a, b) => (votesById[b.client_id] || 0) - (votesById[a.client_id] || 0));

        $('results-feed').innerHTML = rows.map((p, i) => {
            const votes = votesById[p.client_id] || 0;
            const isImposter = knownImposter && p.client_id === knownImposter;
            const tags = [
                isImposter ? '<span class="tag-pill tag-imposter">Imposter</span>' : '',
                !p.is_alive ? '<span class="tag-pill tag-out">Out</span>' : '',
                p.client_id === clientId ? '<span class="tag-pill tag-you">You</span>' : '',
                !p.is_connected ? '<span class="tag-pill tag-offline">Offline</span>' : '',
            ].join('');
            return `
                <li class="result-row ${!p.is_alive ? 'is-out' : ''} ${isImposter ? 'is-imposter' : ''}" style="animation-delay:${i * 50}ms">
                    <span class="bar" data-width="${(votes / maxVotes) * 100}"></span>
                    ${avatarHtml(p.client_id, p.nickname, 'avatar-sm')}
                    <span class="name">${esc(p.nickname)}</span>
                    <span class="tags">${tags}</span>
                    <span class="votes">${votes ? `${votes}×` : '–'}</span>
                </li>`;
        }).join('');
        // Animate the vote bars in on the next frame
        requestAnimationFrame(() => {
            document.querySelectorAll('#results-feed .bar').forEach((bar) => { bar.style.width = `${bar.dataset.width}%`; });
        });

        // Controls
        show('next-round-controls', !over && me.is_host);
        show('game-over-controls', over && me.is_host);
        show('play-again-btn', over && me.is_host);
        show('next-round-wait', !me.is_host);
        $('next-round-wait-text').textContent = over
            ? 'Waiting for the host to start a new game…'
            : 'Waiting for the host to start the next round…';
    }

    function renderSpectatorPanel(state, me) {
        const inRound = ['ROUND_START', 'VOTING', 'RESULTS'].includes(state.state);
        const spectating = inRound && !me.is_alive;
        show('spectator-panel', spectating);
        if (!spectating) return;

        show('reveal-imposter-controls', Boolean(state.mystery_mode && state.can_reveal_imposter && !state.has_revealed));
        show('imposter-revealed-banner', Boolean(state.has_revealed && state.imposter_identity));
        if (state.has_revealed) $('revealed-imposter-name').textContent = state.imposter_identity;
    }

    // --- Boot --------------------------------------------------------------
    if (!nickname) {
        showOnboarding();
    } else {
        const savedRoom = localStorage.getItem(STORAGE.room);
        if (savedRoom) {
            enterGame(savedRoom); // Rejoin after a refresh / accidental close
        } else {
            showLobby();
        }
    }
})();
