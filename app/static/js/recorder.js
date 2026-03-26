/**
 * Screen Recording & Activity Tracking Module v4.0
 *
 * Captures ONLY meaningful process actions:
 *   - Clicks (button, link, menu interactions)
 *   - Selections (dropdowns, checkboxes, radio buttons)
 *   - Data entry (form field input — debounced, no content captured)
 *   - Tab/window switches (app context changes)
 *   - Page navigation (URL changes)
 *   - Copy/paste actions
 *   - Screenshot on meaningful events (not periodic spam)
 *
 * Does NOT capture: scrolling, mouse movement, every keystroke, periodic screenshots
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

            this._addActivity({
                activity_type: 'app_open',
                application: this.sharedScreenLabel,
                window_title: document.title,
                element_text: `Process recording started`,
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

    // ─── Only track meaningful process actions ───

    _startTracking() {
        // 1. CLICKS — buttons, links, menu items, meaningful UI actions
        this._clickHandler = (e) => {
            if (this.isPaused) return;
            const el = e.target;
            const tag = el.tagName?.toLowerCase();
            const role = el.getAttribute('role');
            const type = el.type?.toLowerCase();

            // Determine what kind of click this is
            let actionType = 'click';
            if (tag === 'a' || role === 'link') actionType = 'link_click';
            else if (tag === 'button' || role === 'button' || type === 'submit') actionType = 'button_click';
            else if (tag === 'option' || tag === 'select' || role === 'option') actionType = 'select';
            else if (type === 'checkbox' || type === 'radio') actionType = 'select';
            else if (tag === 'input' || tag === 'textarea') return; // handled by focus/data_entry
            else if (tag === 'li' && el.closest('[role="menu"], [role="listbox"], .dropdown')) actionType = 'menu_select';

            // Get meaningful label
            const label = el.innerText?.trim().substring(0, 150)
                || el.getAttribute('aria-label')
                || el.getAttribute('title')
                || el.getAttribute('value')
                || tag;

            if (!label || label.length < 1) return;

            this._addActivity({
                activity_type: actionType,
                element_text: label,
                element_type: tag,
                url: window.location.href,
                window_title: document.title,
                application: 'Browser',
            });

            // Take a screenshot on meaningful clicks
            this._captureScreenshot(`After: ${label.substring(0, 50)}`);
        };

        // 2. DATA ENTRY — track when user focuses on a form field, debounced
        this._dataEntryTimeout = null;
        this._focusHandler = (e) => {
            if (this.isPaused) return;
            const el = e.target;
            const tag = el.tagName?.toLowerCase();
            if (tag !== 'input' && tag !== 'textarea' && tag !== 'select' && !el.isContentEditable) return;

            // Clear previous timeout — only log once per field interaction
            if (this._dataEntryTimeout) clearTimeout(this._dataEntryTimeout);
            this._dataEntryTimeout = setTimeout(() => {
                const fieldLabel = el.getAttribute('aria-label')
                    || el.getAttribute('placeholder')
                    || el.getAttribute('name')
                    || el.labels?.[0]?.innerText
                    || el.closest('label')?.innerText
                    || `${tag} field`;

                this._addActivity({
                    activity_type: 'data_entry',
                    element_text: `Input: ${fieldLabel.substring(0, 150)}`,
                    element_type: el.type || tag,
                    window_title: document.title,
                    application: 'Browser',
                });
            }, 1500);
        };

        // 3. COPY / PASTE
        this._copyHandler = () => {
            if (this.isPaused) return;
            this._addActivity({
                activity_type: 'copy',
                element_text: 'Content copied to clipboard',
                window_title: document.title,
                application: 'Browser',
            });
        };
        this._pasteHandler = () => {
            if (this.isPaused) return;
            this._addActivity({
                activity_type: 'paste',
                element_text: 'Content pasted from clipboard',
                window_title: document.title,
                application: 'Browser',
            });
        };

        // 4. TAB / WINDOW SWITCH
        this._visibilityHandler = () => {
            if (this.isPaused) return;
            if (document.visibilityState === 'visible') {
                this._addActivity({
                    activity_type: 'tab_switch',
                    element_text: `Switched to: ${document.title}`,
                    window_title: document.title,
                    application: 'Browser',
                });
                this._captureScreenshot(`Tab: ${document.title.substring(0, 40)}`);
            }
        };

        // 5. PAGE NAVIGATION
        this._lastUrl = window.location.href;
        this._urlCheckInterval = setInterval(() => {
            if (this.isPaused) return;
            if (window.location.href !== this._lastUrl) {
                const from = this._lastUrl;
                this._lastUrl = window.location.href;
                this._addActivity({
                    activity_type: 'navigation',
                    url: window.location.href,
                    element_text: `Navigated to: ${document.title || window.location.pathname}`,
                    window_title: document.title,
                    application: 'Browser',
                    metadata: { from_url: from },
                });
                this._captureScreenshot(`Page: ${document.title.substring(0, 40)}`);
            }
        }, 800);

        // 6. FORM SUBMISSIONS
        this._submitHandler = (e) => {
            if (this.isPaused) return;
            const form = e.target;
            this._addActivity({
                activity_type: 'form_submit',
                element_text: `Form submitted: ${form.getAttribute('name') || form.getAttribute('id') || 'form'}`,
                window_title: document.title,
                application: 'Browser',
            });
            this._captureScreenshot('Form submitted');
        };

        document.addEventListener('click', this._clickHandler, true);
        document.addEventListener('focusin', this._focusHandler, true);
        document.addEventListener('copy', this._copyHandler, true);
        document.addEventListener('paste', this._pasteHandler, true);
        document.addEventListener('visibilitychange', this._visibilityHandler);
        document.addEventListener('submit', this._submitHandler, true);
    }

    _stopTracking() {
        document.removeEventListener('click', this._clickHandler, true);
        document.removeEventListener('focusin', this._focusHandler, true);
        document.removeEventListener('copy', this._copyHandler, true);
        document.removeEventListener('paste', this._pasteHandler, true);
        document.removeEventListener('visibilitychange', this._visibilityHandler);
        document.removeEventListener('submit', this._submitHandler, true);
        clearInterval(this._urlCheckInterval);
        clearInterval(this._screenCheckInterval);
        if (this._dataEntryTimeout) clearTimeout(this._dataEntryTimeout);
    }

    // ─── Screen change detection (captures when the visible screen changes) ───

    _startScreenChangeDetection() {
        // Check every 2s if the screen content has visually changed — only capture on change
        this._screenCheckInterval = setInterval(async () => {
            if (!this.isRecording || !this.mediaStream || this.isPaused) return;
            try {
                const track = this.mediaStream.getVideoTracks()[0];
                if (!track || track.readyState !== 'live') return;

                // Draw current frame
                if (typeof ImageCapture !== 'undefined') {
                    const capture = new ImageCapture(track);
                    const bitmap = await capture.grabFrame();
                    this.canvas.width = Math.min(bitmap.width, 320); // small for comparison
                    this.canvas.height = Math.min(bitmap.height, 180);
                    this.canvasCtx.drawImage(bitmap, 0, 0, this.canvas.width, this.canvas.height);
                    bitmap.close();
                } else if (this.videoElement) {
                    this.canvas.width = 320;
                    this.canvas.height = 180;
                    this.canvasCtx.drawImage(this.videoElement, 0, 0, 320, 180);
                }

                // Update mini-map
                const thumbnail = document.getElementById('miniMapThumb');
                if (thumbnail) thumbnail.src = this.canvas.toDataURL('image/jpeg', 0.3);

                // Simple change detection: compare a hash of sampled pixels
                const imageData = this.canvasCtx.getImageData(0, 0, this.canvas.width, this.canvas.height);
                const hash = this._quickHash(imageData.data);

                if (hash !== this._lastScreenHash) {
                    this._lastScreenHash = hash;
                    // Screen changed visibly — log it
                    this._addActivity({
                        activity_type: 'screen_change',
                        application: this.sharedScreenLabel,
                        window_title: document.title,
                        element_text: `Screen content changed`,
                    });
                }
            } catch (err) { /* skip */ }
        }, 2000);
    }

    _quickHash(pixelData) {
        // Sample every 500th pixel for a quick visual fingerprint
        let hash = 0;
        for (let i = 0; i < pixelData.length; i += 2000) {
            hash = ((hash << 5) - hash + pixelData[i]) | 0;
        }
        return hash;
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
            button_click: '🖱️', link_click: '🔗', click: '👆', select: '☑️',
            menu_select: '📋', data_entry: '⌨️', navigation: '🧭', tab_switch: '🔄',
            copy: '📋', paste: '📌', form_submit: '📤', screen_change: '🖥️',
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

        this.timerInterval = setInterval(() => {
            if (this.isPaused) return;
            const elapsed = Math.floor((Date.now() - this.startTime - this.pausedTime) / 1000);

            // Auto-stop at 30 minutes (1800 seconds)
            if (elapsed >= 1800) {
                if (window.showToast) {
                    window.showToast('Maximum recording time of 30 minutes reached. Stopping automatically.', 'warning');
                } else {
                    alert('Maximum recording time of 30 minutes reached. Stopping automatically.');
                }
                if (this.isRecording && typeof window.stopRecording === 'function') {
                    window.stopRecording();
                }
                return;
            }

            const hrs = String(Math.floor(elapsed / 3600)).padStart(2, '0');
            const mins = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
            const secs = String(elapsed % 60).padStart(2, '0');
            if (timerEl) timerEl.textContent = `${hrs}:${mins}:${secs}`;
        }, 1000);
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
