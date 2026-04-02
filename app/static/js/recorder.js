/**
 * Screen Recording & Activity Tracking Module v5.0
 *
 * Captures ALL user actions including:
 *   - Every screen/page/tab/window change with screenshot
 *   - Window focus & blur (app switches, Alt+Tab, Cmd+Tab)
 *   - Window title changes (detects navigation to new app/screen)
 *   - Clicks (button, link, menu, any element)
 *   - Selections (dropdowns, checkboxes, radio buttons)
 *   - Data entry (form fields — debounced)
 *   - Page navigation & URL changes
 *   - Copy/paste actions
 *   - Form submissions
 *   - Keyboard shortcuts (Alt, Ctrl, Cmd combos)
 *   - Screenshot on EVERY screen change and meaningful action
 */

class ScreenRecorder {
    constructor() {
        this.mediaStream = null;
        this.recordingId = null;
        this.isRecording = false;
        this.isPaused = false;
        this.activityBuffer = [];
        this.activityCount = 0;
        this.startTime = null;
        this.pausedTime = 0;
        this.pauseStart = null;
        this.timerInterval = null;
        this.canvas = document.createElement('canvas');
        this.canvasCtx = this.canvas.getContext('2d');
        this.videoElement = null;
        this.recentActivities = [];
        this.sharedScreenLabel = 'Screen';
        this._lastWindowTitle = '';
        this._lastScreenHash = '';
        this._screenCheckInterval = null;
    }

    async startRecording(title) {
        try {
            this.mediaStream = await navigator.mediaDevices.getDisplayMedia({
                video: { cursor: 'always', displaySurface: 'monitor' },
                audio: false,
            });

            const videoTrack = this.mediaStream.getVideoTracks()[0];
            this.sharedScreenLabel = videoTrack.label || 'Screen';

            this.videoElement = document.getElementById('previewVideo');
            if (this.videoElement) {
                this.videoElement.srcObject = this.mediaStream;
                document.getElementById('screenPreview').style.display = 'block';
            }

            const resp = await fetch('/api/recordings/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: title || 'Untitled Recording' }),
            });
            const data = await resp.json();
            this.recordingId = data.id;
            this.isRecording = true;
            this.isPaused = false;
            this.activityCount = 0;
            this.pausedTime = 0;
            this.startTime = Date.now();
            this.recentActivities = [];
            this._lastWindowTitle = document.title;
            this._lastUrl = window.location.href;

            this._addActivity({
                activity_type: 'app_open',
                application: this.sharedScreenLabel,
                window_title: document.title,
                url: window.location.href,
                element_text: `Process recording started — Screen: ${document.title}`,
            });

            this._startTracking();
            this._startScreenChangeDetection();
            this._startTimer();

            videoTrack.onended = () => {
                if (this.isRecording) window.stopRecording();
            };

            return this.recordingId;
        } catch (err) {
            console.error('Failed to start recording:', err);
            throw err;
        }
    }

    pauseRecording() {
        if (!this.isRecording || this.isPaused) return;
        this.isPaused = true;
        this.pauseStart = Date.now();
        this._addActivity({ activity_type: 'pause', application: 'Process Extractor', window_title: 'Paused', element_text: 'Recording paused' });
    }

    resumeRecording() {
        if (!this.isRecording || !this.isPaused) return;
        this.isPaused = false;
        if (this.pauseStart) { this.pausedTime += Date.now() - this.pauseStart; this.pauseStart = null; }
        this._addActivity({ activity_type: 'resume', application: 'Process Extractor', window_title: 'Resumed', element_text: 'Recording resumed' });
    }

    async stopRecording() {
        if (!this.isRecording) return null;
        this.isRecording = false;
        this.isPaused = false;

        this._addActivity({
            activity_type: 'app_close',
            application: this.sharedScreenLabel,
            window_title: document.title,
            element_text: `Process recording ended — ${this.activityCount} actions captured`,
        });

        await this._flushActivities();
        if (this.mediaStream) this.mediaStream.getTracks().forEach(t => t.stop());
        clearInterval(this.timerInterval);
        clearInterval(this._screenCheckInterval);
        if (this.videoElement) { this.videoElement.srcObject = null; document.getElementById('screenPreview').style.display = 'none'; }
        this._stopTracking();

        const resp = await fetch(`/api/recordings/${this.recordingId}/stop`, { method: 'POST' });
        return await resp.json();
    }

    // ─── Track ALL user actions ───

    _startTracking() {
        // 1. CLICKS — capture every click with full context
        this._clickHandler = (e) => {
            if (this.isPaused) return;
            const el = e.target;
            const tag = el.tagName?.toLowerCase();
            const role = el.getAttribute('role');
            const type = el.type?.toLowerCase();

            let actionType = 'click';
            if (tag === 'a' || role === 'link') actionType = 'link_click';
            else if (tag === 'button' || role === 'button' || type === 'submit') actionType = 'button_click';
            else if (tag === 'option' || tag === 'select' || role === 'option') actionType = 'select';
            else if (type === 'checkbox') actionType = el.checked ? 'checkbox_checked' : 'checkbox_unchecked';
            else if (type === 'radio') actionType = 'radio_selected';
            else if (tag === 'li' && el.closest('[role="menu"],[role="listbox"],.dropdown')) actionType = 'menu_select';

            const label = el.innerText?.trim().substring(0, 150)
                || el.getAttribute('aria-label')
                || el.getAttribute('title')
                || el.getAttribute('value')
                || el.getAttribute('name')
                || tag;

            if (!label || label.length < 1) return;

            // Build context path (e.g. "Sidebar > Menu > Orders")
            const path = this._getElementPath(el);

            this._addActivity({
                activity_type: actionType,
                element_text: `${label}${path ? ` (in: ${path})` : ''}`,
                element_type: tag,
                url: window.location.href,
                window_title: document.title,
                application: this._getAppName(),
            });

            this._captureScreenshot(`Clicked: ${label.substring(0, 50)}`);
        };

        // 2. DATA ENTRY — capture field name + value on blur (when user leaves field)
        this._dataEntryTimeout = null;
        this._blurHandler = (e) => {
            if (this.isPaused) return;
            const el = e.target;
            const tag = el.tagName?.toLowerCase();
            if (tag !== 'input' && tag !== 'textarea' && tag !== 'select' && !el.isContentEditable) return;

            const fieldLabel = el.getAttribute('aria-label')
                || el.getAttribute('placeholder')
                || el.getAttribute('name')
                || el.getAttribute('id')
                || el.labels?.[0]?.innerText?.trim()
                || el.closest('label')?.innerText?.trim()
                || `${el.type || tag} field`;

            const value = el.type === 'password' ? '(password)'
                : el.tagName?.toLowerCase() === 'select' ? el.options[el.selectedIndex]?.text
                : el.value?.substring(0, 100) || '';

            if (!value && !fieldLabel) return;

            this._addActivity({
                activity_type: 'data_entry',
                element_text: `Field "${fieldLabel}"${value ? `: entered "${value}"` : ''}`,
                element_type: el.type || tag,
                url: window.location.href,
                window_title: document.title,
                application: this._getAppName(),
            });
            this._captureScreenshot(`Filled: ${fieldLabel.substring(0, 40)}`);
        };

        // 3. DROPDOWN CHANGE
        this._changeHandler = (e) => {
            if (this.isPaused) return;
            const el = e.target;
            if (el.tagName?.toLowerCase() !== 'select') return;
            const label = el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('id') || 'dropdown';
            const value = el.options[el.selectedIndex]?.text || el.value;
            this._addActivity({
                activity_type: 'select',
                element_text: `Selected "${value}" from "${label}"`,
                element_type: 'select',
                url: window.location.href,
                window_title: document.title,
                application: this._getAppName(),
            });
            this._captureScreenshot(`Selected: ${value.substring(0, 40)}`);
        };

        // 4. COPY / PASTE
        this._copyHandler = () => {
            if (this.isPaused) return;
            const sel = window.getSelection()?.toString()?.substring(0, 80) || '';
            this._addActivity({
                activity_type: 'copy',
                element_text: sel ? `Copied: "${sel}"` : 'Content copied to clipboard',
                window_title: document.title,
                application: this._getAppName(),
            });
        };
        this._pasteHandler = () => {
            if (this.isPaused) return;
            this._addActivity({
                activity_type: 'paste',
                element_text: 'Content pasted from clipboard',
                window_title: document.title,
                url: window.location.href,
                application: this._getAppName(),
            });
        };

        // 5. KEYBOARD SHORTCUTS (Alt+Tab, Ctrl+combos, Cmd+combos)
        this._keydownHandler = (e) => {
            if (this.isPaused) return;
            const key = e.key;
            const isModifier = e.altKey || e.ctrlKey || e.metaKey;
            if (!isModifier) return;

            // Alt+Tab or Cmd+Tab = app switch
            if ((e.altKey || e.metaKey) && key === 'Tab') {
                this._addActivity({
                    activity_type: 'app_switch',
                    element_text: `App switch shortcut pressed (${e.altKey ? 'Alt' : 'Cmd'}+Tab) — switching away from: ${document.title}`,
                    window_title: document.title,
                    application: this._getAppName(),
                });
                setTimeout(() => this._captureScreenshot('After app switch'), 600);
                return;
            }

            // Only log other meaningful shortcuts
            const skip = ['Shift','Alt','Control','Meta','CapsLock','Escape'];
            if (skip.includes(key)) return;

            const modStr = [e.ctrlKey && 'Ctrl', e.metaKey && 'Cmd', e.altKey && 'Alt', e.shiftKey && 'Shift']
                .filter(Boolean).join('+');
            this._addActivity({
                activity_type: 'keyboard_shortcut',
                element_text: `Shortcut: ${modStr}+${key}`,
                window_title: document.title,
                application: this._getAppName(),
            });
        };

        // 6. TAB / WINDOW VISIBILITY CHANGE (user switches to another tab or app)
        this._visibilityHandler = () => {
            if (this.isPaused) return;
            if (document.visibilityState === 'hidden') {
                this._addActivity({
                    activity_type: 'screen_leave',
                    element_text: `Left screen: "${document.title}" — switched to another app or tab`,
                    window_title: document.title,
                    url: window.location.href,
                    application: this._getAppName(),
                });
            } else {
                this._addActivity({
                    activity_type: 'screen_return',
                    element_text: `Returned to: "${document.title}"`,
                    window_title: document.title,
                    url: window.location.href,
                    application: this._getAppName(),
                });
                this._captureScreenshot(`Returned to: ${document.title.substring(0, 40)}`);
            }
        };

        // 7. WINDOW FOCUS / BLUR (app-level switch detection)
        this._focusHandler = () => {
            if (this.isPaused) return;
            this._addActivity({
                activity_type: 'window_focus',
                element_text: `Window focused: "${document.title}"`,
                window_title: document.title,
                url: window.location.href,
                application: this._getAppName(),
            });
            this._captureScreenshot(`Window: ${document.title.substring(0, 40)}`);
        };
        this._blurWindowHandler = () => {
            if (this.isPaused) return;
            this._addActivity({
                activity_type: 'window_blur',
                element_text: `Window lost focus: "${document.title}" — user switched to another application`,
                window_title: document.title,
                url: window.location.href,
                application: this._getAppName(),
            });
        };

        // 8. PAGE NAVIGATION (URL changes — SPA + traditional)
        this._urlCheckInterval = setInterval(() => {
            if (this.isPaused) return;
            const currentUrl = window.location.href;
            const currentTitle = document.title;

            if (currentUrl !== this._lastUrl) {
                const fromUrl = this._lastUrl;
                const fromTitle = this._lastWindowTitle;
                this._lastUrl = currentUrl;
                this._lastWindowTitle = currentTitle;
                this._addActivity({
                    activity_type: 'navigation',
                    url: currentUrl,
                    element_text: `Navigated from "${fromTitle}" → "${currentTitle || window.location.pathname}"`,
                    window_title: currentTitle,
                    application: this._getAppName(),
                    metadata: { from_url: fromUrl, to_url: currentUrl },
                });
                this._captureScreenshot(`Page: ${currentTitle.substring(0, 40)}`);
            } else if (currentTitle !== this._lastWindowTitle) {
                // Title changed without URL change — common in SPAs and desktop apps
                const fromTitle = this._lastWindowTitle;
                this._lastWindowTitle = currentTitle;
                this._addActivity({
                    activity_type: 'screen_change',
                    url: currentUrl,
                    element_text: `Screen changed: "${fromTitle}" → "${currentTitle}"`,
                    window_title: currentTitle,
                    application: this._getAppName(),
                });
                this._captureScreenshot(`Screen: ${currentTitle.substring(0, 40)}`);
            }
        }, 400);

        // 9. FORM SUBMISSIONS
        this._submitHandler = (e) => {
            if (this.isPaused) return;
            const form = e.target;
            const formName = form.getAttribute('name') || form.getAttribute('id') || form.getAttribute('action') || 'form';
            this._addActivity({
                activity_type: 'form_submit',
                element_text: `Form submitted: "${formName}" on "${document.title}"`,
                url: window.location.href,
                window_title: document.title,
                application: this._getAppName(),
            });
            this._captureScreenshot(`After submit: ${formName.substring(0, 40)}`);
        };

        document.addEventListener('click',        this._clickHandler,      true);
        document.addEventListener('focusout',     this._blurHandler,       true);
        document.addEventListener('change',       this._changeHandler,     true);
        document.addEventListener('copy',         this._copyHandler,       true);
        document.addEventListener('paste',        this._pasteHandler,      true);
        document.addEventListener('keydown',      this._keydownHandler,    true);
        document.addEventListener('visibilitychange', this._visibilityHandler);
        document.addEventListener('submit',       this._submitHandler,     true);
        window.addEventListener('focus',          this._focusHandler);
        window.addEventListener('blur',           this._blurWindowHandler);
    }

    _stopTracking() {
        document.removeEventListener('click',         this._clickHandler,      true);
        document.removeEventListener('focusout',      this._blurHandler,       true);
        document.removeEventListener('change',        this._changeHandler,     true);
        document.removeEventListener('copy',          this._copyHandler,       true);
        document.removeEventListener('paste',         this._pasteHandler,      true);
        document.removeEventListener('keydown',       this._keydownHandler,    true);
        document.removeEventListener('visibilitychange', this._visibilityHandler);
        document.removeEventListener('submit',        this._submitHandler,     true);
        window.removeEventListener('focus',           this._focusHandler);
        window.removeEventListener('blur',            this._blurWindowHandler);
        clearInterval(this._urlCheckInterval);
        clearInterval(this._screenCheckInterval);
        if (this._dataEntryTimeout) clearTimeout(this._dataEntryTimeout);
    }

    // Get a clean app name from current URL
    _getAppName() {
        try {
            const host = new URL(window.location.href).hostname;
            return host || document.title || 'Browser';
        } catch { return document.title || 'Browser'; }
    }

    // Build a breadcrumb path for an element (e.g. "Nav > Sidebar > Orders")
    _getElementPath(el) {
        const parts = [];
        let current = el.parentElement;
        let depth = 0;
        while (current && depth < 4) {
            const id = current.getAttribute('id');
            const role = current.getAttribute('role');
            const label = current.getAttribute('aria-label');
            const tag = current.tagName?.toLowerCase();
            if (label) { parts.unshift(label); break; }
            if (id && !/^\d/.test(id)) parts.unshift(id);
            else if (role && !['presentation','none','group'].includes(role)) parts.unshift(role);
            current = current.parentElement;
            depth++;
        }
        return parts.slice(0, 3).join(' > ');
    }

    // ─── Screen change detection (captures when the visible screen changes) ───

    _startScreenChangeDetection() {
        let _screenshotPending = false;

        // Check every 500ms — fast enough to catch quick app/screen switches
        this._screenCheckInterval = setInterval(async () => {
            if (!this.isRecording || !this.mediaStream || this.isPaused) return;
            try {
                const track = this.mediaStream.getVideoTracks()[0];
                if (!track || track.readyState !== 'live') return;

                if (typeof ImageCapture !== 'undefined') {
                    const capture = new ImageCapture(track);
                    const bitmap = await capture.grabFrame();
                    this.canvas.width = Math.min(bitmap.width, 320);
                    this.canvas.height = Math.min(bitmap.height, 180);
                    this.canvasCtx.drawImage(bitmap, 0, 0, this.canvas.width, this.canvas.height);
                    bitmap.close();
                } else if (this.videoElement) {
                    this.canvas.width = 320;
                    this.canvas.height = 180;
                    this.canvasCtx.drawImage(this.videoElement, 0, 0, 320, 180);
                }

                // Update mini-map thumbnail
                const thumbnail = document.getElementById('miniMapThumb');
                if (thumbnail) {
                    thumbnail.src = this.canvas.toDataURL('image/jpeg', 0.3);
                    document.getElementById('miniMap').style.display = 'block';
                }

                const imageData = this.canvasCtx.getImageData(0, 0, this.canvas.width, this.canvas.height);
                const hash = this._quickHash(imageData.data);

                if (hash !== this._lastScreenHash) {
                    const prevHash = this._lastScreenHash;
                    this._lastScreenHash = hash;

                    // Only log if hash changed significantly (not just a cursor blink)
                    const changeMagnitude = this._hashDiff(prevHash, hash);
                    if (changeMagnitude < 10 && prevHash !== '') return; // tiny change, skip

                    this._addActivity({
                        activity_type: 'screen_change',
                        application: this.sharedScreenLabel,
                        window_title: document.title,
                        url: window.location.href,
                        element_text: `Screen changed — now showing: "${document.title}" (${this._getAppName()})`,
                    });

                    // Take a full-res screenshot of the new screen (throttled — max 1 per second)
                    if (!_screenshotPending) {
                        _screenshotPending = true;
                        setTimeout(async () => {
                            await this._captureScreenshot(`Screen: ${document.title.substring(0, 40)}`);
                            _screenshotPending = false;
                        }, 300);
                    }
                }
            } catch (err) { /* skip */ }
        }, 500);
    }

    _quickHash(pixelData) {
        let hash = 0;
        for (let i = 0; i < pixelData.length; i += 1000) {
            hash = ((hash << 5) - hash + pixelData[i] + pixelData[i+1] + pixelData[i+2]) | 0;
        }
        return hash;
    }

    _hashDiff(a, b) {
        // Returns a rough magnitude of change between two hashes
        if (!a || !b) return 999;
        return Math.abs((a >>> 0) - (b >>> 0)) / 1000000;
    }

    async _captureScreenshot(reason) {
        if (!this.isRecording || !this.mediaStream || this.isPaused) return;
        try {
            const track = this.mediaStream.getVideoTracks()[0];
            if (!track || track.readyState !== 'live') return;

            const captureCanvas = document.createElement('canvas');
            const ctx = captureCanvas.getContext('2d');

            if (typeof ImageCapture !== 'undefined') {
                const capture = new ImageCapture(track);
                const bitmap = await capture.grabFrame();
                captureCanvas.width = bitmap.width;
                captureCanvas.height = bitmap.height;
                ctx.drawImage(bitmap, 0, 0);
                bitmap.close();
            } else if (this.videoElement) {
                captureCanvas.width = this.videoElement.videoWidth;
                captureCanvas.height = this.videoElement.videoHeight;
                ctx.drawImage(this.videoElement, 0, 0);
            }

            const screenshot = captureCanvas.toDataURL('image/png', 0.4);

            // Send screenshot directly (not via buffer — too large)
            try {
                await fetch(`/api/recordings/${this.recordingId}/activity`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        activity_type: 'screenshot',
                        screenshot: screenshot,
                        application: this.sharedScreenLabel,
                        window_title: document.title,
                        element_text: reason || 'Screenshot',
                        timestamp: new Date().toISOString(),
                    }),
                });
            } catch (err) { /* drop silently */ }
        } catch (err) { /* skip */ }
    }

    // ─── Buffer & transport ───

    _addActivity(activity) {
        if (this.isPaused && activity.activity_type !== 'pause' && activity.activity_type !== 'resume') return;

        activity.timestamp = new Date().toISOString();
        this.activityBuffer.push(activity);
        this.activityCount++;
        this._updateActivityCounter();
        this._updateActivityList(activity);

        if (this.activityBuffer.length >= 5) {
            this._flushActivities();
        }
    }

    _updateActivityList(activity) {
        this.recentActivities.unshift(activity);
        if (this.recentActivities.length > 15) this.recentActivities.pop();

        const listEl = document.getElementById('activityFeed');
        if (!listEl) return;

        const typeIcons = {
            button_click: '🖱️', link_click: '🔗', click: '👆',
            select: '☑️', checkbox_checked: '✅', checkbox_unchecked: '⬜', radio_selected: '🔘',
            menu_select: '📋', data_entry: '⌨️', navigation: '🧭',
            screen_leave: '👋', screen_return: '🔙', screen_change: '🖥️',
            window_focus: '🪟', window_blur: '💨', tab_switch: '🔄',
            app_switch: '↔️', keyboard_shortcut: '⌨️',
            copy: '📋', paste: '📌', form_submit: '📤',
            screenshot: '📸', app_open: '▶️', app_close: '⏹️', pause: '⏸️', resume: '▶️',
        };

        let html = '';
        this.recentActivities.slice(0, 10).forEach(act => {
            const icon = typeIcons[act.activity_type] || '📌';
            const time = new Date(act.timestamp).toLocaleTimeString();
            const desc = act.element_text || act.window_title || act.activity_type;
            html += `<div class="activity-feed-item">
                <span class="feed-icon">${icon}</span>
                <span class="feed-time">${time}</span>
                <span class="feed-desc">${desc.substring(0, 60)}</span>
            </div>`;
        });
        listEl.innerHTML = html;
    }

    async _flushActivities() {
        if (this.activityBuffer.length === 0) return;
        const activities = [...this.activityBuffer];
        this.activityBuffer = [];

        try {
            await fetch(`/api/recordings/${this.recordingId}/batch-activities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ activities }),
            });
        } catch (err) {
            this.activityBuffer = [...activities, ...this.activityBuffer];
        }
    }

    _startTimer() {
        const timerEl = document.getElementById('timer');
        if (timerEl) timerEl.style.display = 'inline';
        this._warned5min = false;
        this._warned2min = false;
        this._warned1min = false;

        this.timerInterval = setInterval(() => {
            if (this.isPaused) return;
            const elapsed = Math.floor((Date.now() - this.startTime - this.pausedTime) / 1000);
            const remaining = 1800 - elapsed;

            // ── Auto-stop at exactly 30 minutes ──────────────────────────────
            if (elapsed >= 1800) {
                clearInterval(this.timerInterval);
                this._showTimeLimitBanner();
                if (this.isRecording && typeof window.stopRecording === 'function') {
                    window.stopRecording();
                }
                return;
            }

            // ── Countdown warnings ────────────────────────────────────────────
            if (!this._warned5min && remaining <= 300) {
                this._warned5min = true;
                if (window.showToast) window.showToast('⏱ 5 minutes left — recording will auto-stop and analyze at 30 min.', 'warning');
                if (timerEl) timerEl.style.color = '#F59E0B';
            }
            if (!this._warned2min && remaining <= 120) {
                this._warned2min = true;
                if (window.showToast) window.showToast('⚠️ 2 minutes remaining before auto-stop!', 'warning');
                if (timerEl) timerEl.style.color = '#EF4444';
            }
            if (!this._warned1min && remaining <= 60) {
                this._warned1min = true;
                if (window.showToast) window.showToast('🔴 1 minute left — recording stops at 30:00', 'warning');
            }

            const hrs  = String(Math.floor(elapsed / 3600)).padStart(2, '0');
            const mins = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
            const secs = String(elapsed % 60).padStart(2, '0');
            if (timerEl) timerEl.textContent = `${hrs}:${mins}:${secs}`;
        }, 1000);
    }

    _showTimeLimitBanner() {
        // Remove any existing banner
        const old = document.getElementById('timeLimitBanner');
        if (old) old.remove();

        const banner = document.createElement('div');
        banner.id = 'timeLimitBanner';
        banner.style.cssText = `
            position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
            background: linear-gradient(135deg, #7C3AED, #4F46E5);
            color: white; padding: 18px 24px;
            display: flex; align-items: center; justify-content: center; gap: 14px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.25);
            font-family: inherit; animation: slideDown 0.3s ease;
        `;
        banner.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" style="flex-shrink:0;">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
            </svg>
            <div>
                <div style="font-size:15px;font-weight:800;">30-minute recording limit reached</div>
                <div style="font-size:13px;opacity:0.85;margin-top:2px;">Stopping recording and starting AI analysis — please wait...</div>
            </div>
            <div style="margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;opacity:0.85;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                Analyzing...
            </div>
        `;

        // Add slide-down animation
        const style = document.createElement('style');
        style.textContent = '@keyframes slideDown { from { transform: translateY(-100%); } to { transform: translateY(0); } }';
        document.head.appendChild(style);
        document.body.prepend(banner);
    }

    _updateActivityCounter() {
        const counterEl = document.getElementById('activityCount');
        if (counterEl) {
            counterEl.style.display = 'inline';
            counterEl.textContent = `${this.activityCount} actions`;
        }
    }
}

const screenRecorder = new ScreenRecorder();
