(() => {
  // Menu dropdowns: only one open at a time; a click outside or Escape closes it.
  const menus = [...document.querySelectorAll('.nav-menu')];
  menus.forEach(menu => menu.addEventListener('toggle', () => {
    if (menu.open) menus.forEach(other => { if (other !== menu) other.open = false; });
  }));
  document.addEventListener('click', event => {
    menus.forEach(menu => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = menus.find(menu => menu.open);
    if (open) { open.open = false; open.querySelector('summary').focus(); }
  });

  // Item type picker: main types fold open to show their sub-types; a "find a type" box; the closed
  // dropdown lists what's ticked and each main type shows how many of its sub-types are ticked.
  document.querySelectorAll('[data-category-picker]').forEach(picker => {
    const boxes = [...picker.querySelectorAll('input[type=checkbox]')];
    const groups = [...picker.querySelectorAll('[data-category-group]')];
    const picked = picker.querySelector('[data-picked]');
    const filter = picker.querySelector('[data-category-filter]');
    const none = picker.querySelector('[data-category-none]');
    const summarize = () => {
      const names = boxes.filter(box => box.checked).map(box => box.dataset.full);
      picked.textContent = names.length ? names.join(', ') : picked.dataset.empty;
      groups.forEach(group => {
        const subs = [...group.querySelectorAll('input[type=checkbox]')];
        const ticked = subs.filter(box => box.checked).length;
        const count = group.querySelector('[data-count]');
        if (subs.length) count.textContent = ticked ? `${ticked} ticked` : `${subs.length} sub-type${subs.length === 1 ? '' : 's'}`;
      });
    };
    boxes.forEach(box => box.addEventListener('change', summarize));
    if (boxes.length > 8) {
      filter.hidden = false;
      const wasOpen = new Map(groups.map(group => [group, group.open]));
      filter.addEventListener('input', () => {
        const words = filter.value.trim().toLowerCase();
        let shown = 0;
        groups.forEach(group => {
          const mainMatches = group.querySelector('summary').textContent.toLowerCase().includes(words);
          const subs = [...group.querySelectorAll('.category-sub')];
          // A matching main type shows all its sub-types; otherwise only the sub-types that match.
          subs.forEach(sub => { sub.hidden = !(mainMatches || sub.textContent.toLowerCase().includes(words)); });
          const visible = mainMatches || subs.some(sub => !sub.hidden);
          group.hidden = !visible;
          group.open = words ? visible && subs.some(sub => !sub.hidden) : wasOpen.get(group);
          if (visible) shown += 1;
        });
        none.hidden = shown > 0;
      });
      groups.forEach(group => group.addEventListener('toggle', () => { if (!filter.value) wasOpen.set(group, group.open); }));
      // Enter in the find box shouldn't submit the whole item form.
      filter.addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
    }
  });

  const timer = document.querySelector('[data-timer]');
  if (timer) {
    const startedAt = Date.now();
    const base = Number(timer.dataset.seconds || 0);
    const running = timer.dataset.running === 'true';
    function render() {
      const n = Math.max(0, base + (running ? Math.floor((Date.now() - startedAt) / 1000) : 0));
      const h = String(Math.floor(n / 3600)).padStart(2, '0');
      const m = String(Math.floor((n % 3600) / 60)).padStart(2, '0');
      const s = String(n % 60).padStart(2, '0');
      timer.textContent = `${h}:${m}:${s}`;
    }
    render(); if (running) window.setInterval(render, 1000);
  }
  // Expense form: show the miles boxes only for mileage, and the amount box only for everything else.
  const categories = document.querySelectorAll('input[name="category"]');
  if (categories.length) {
    const sync = () => {
      const picked = document.querySelector('input[name="category"]:checked')?.value;
      document.querySelectorAll('[data-show-for]').forEach(el => { el.hidden = el.dataset.showFor !== picked; });
      document.querySelectorAll('[data-hide-for]').forEach(el => { el.hidden = el.dataset.hideFor === picked; });
    };
    categories.forEach(input => input.addEventListener('change', sync));
    sync();
  }
  // Add/edit item: the bin location updates the SKU preview, is checked against open slots as you type,
  // and "Bin full → next bin" jumps to the next bin's first open slot. Nothing is reserved until Save.
  const slotPicker = document.querySelector('[data-slot-picker]');
  if (slotPicker) {
    const input = slotPicker.querySelector('[data-location]');
    const sku = slotPicker.querySelector('[data-sku-preview]');
    const nextBin = slotPicker.querySelector('[data-next-bin]');
    const field = input.closest('.field');
    const error = document.createElement('small');
    error.className = 'field-error';
    error.hidden = true;
    input.after(error);
    const normalize = text => {
      const match = text.trim().toUpperCase().match(/^([A-Z]+)(\d{1,2})$/);
      return match ? match[1] + match[2].padStart(2, '0') : text.trim().toUpperCase();
    };
    const query = params => new URLSearchParams({...params, ...(input.dataset.item ? {item: input.dataset.item} : {})});
    const showSku = location => { if (sku) sku.value = location ? `${location}-${sku.dataset.itemNumber}` : ''; };
    const showError = message => {
      error.textContent = message || '';
      error.hidden = !message;
      field.classList.toggle('has-error', Boolean(message));
    };
    let pending;
    const check = () => {
      const location = normalize(input.value);
      if (!location) { showError(''); return; }
      fetch(`${slotPicker.dataset.checkUrl}?${query({location})}`)
        .then(response => response.json())
        .then(data => { if (normalize(input.value) === data.location) showError(data.error); })
        .catch(() => {});
    };
    input.addEventListener('input', () => {
      showSku(normalize(input.value));
      window.clearTimeout(pending);
      pending = window.setTimeout(check, 300);
    });
    input.addEventListener('change', () => { input.value = normalize(input.value); showSku(input.value); });
    showSku(normalize(input.value));
    if (nextBin) nextBin.addEventListener('click', () => {
      fetch(`${slotPicker.dataset.nextBinUrl}?${query({after: normalize(input.value)})}`)
        .then(response => response.json())
        .then(data => { input.value = data.location; showSku(data.location); showError(''); })
        .catch(() => showError('Couldn’t find the next bin. Check your connection and try again.'));
    });
  }

  // Photo boxes: shrink photos on the phone (1600px JPEG) before uploading, so several upload quickly on cell
  // service. The server shrinks them too, so if anything here fails the originals are sent instead.
  const PHOTO_MAX_SIDE = 1600;
  const shrinkPhoto = async file => {
    if (!file.type.startsWith('image/') || file.type === 'image/gif') return file;
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d');
    context.fillStyle = '#fff';  // transparent PNGs go on white rather than black
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.85));
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg', lastModified: file.lastModified});
  };
  document.querySelectorAll('input[type=file][multiple][accept^="image"]').forEach(input => {
    input.addEventListener('change', async () => {
      if (!input.files.length || typeof createImageBitmap !== 'function' || typeof DataTransfer !== 'function') return;
      const saveButtons = [...input.form.querySelectorAll('button:not([type=button])')];
      saveButtons.forEach(button => { button.disabled = true; });
      try {
        const smaller = new DataTransfer();
        for (const file of input.files) {  // one at a time: ten full-size photos at once can run a phone out of memory
          smaller.items.add(await shrinkPhoto(file).catch(() => file));
        }
        input.files = smaller.files;
      } catch (error) {
        // keep the original photos
      } finally {
        saveButtons.forEach(button => { button.disabled = false; });
      }
    });
  });

  const chart = document.querySelector('#profit-chart');
  const dataTag = document.querySelector('#profit-data');
  if (chart && dataTag) {
    const points = JSON.parse(dataTag.textContent);
    const values = points.map(p => Number(p.value));
    if (!values.length) return;
    const width = 700, top = 15, bottom = 232, left = 8, right = 692;
    let lo = Math.min(0,...values), hi = Math.max(0,...values);
    if (lo === hi) { lo -= 1; hi += 1; }
    const range = hi - lo, pad = range * .12;
    lo -= pad; hi += pad;
    const x = i => points.length === 1 ? (left + right)/2 : left + i * (right-left)/(points.length-1);
    const y = v => bottom-(v-lo)/(hi-lo)*(bottom-top);
    const coords = values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
    const zero = y(0);
    const area = `M ${x(0)} ${zero} L ${values.map((v,i)=>`${x(i)} ${y(v)}`).join(' L ')} L ${x(values.length-1)} ${zero} Z`;
    const primary = getComputedStyle(document.documentElement).getPropertyValue('--primary').trim();
    const ns = 'http://www.w3.org/2000/svg';
    const make = (tag, attrs) => { const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));chart.appendChild(el);return el; };
    for(let i=0;i<4;i++) { const yy=top+i*(bottom-top)/3; make('line',{x1:0,y1:yy,x2:width,y2:yy,stroke:'#E2E8F0','stroke-dasharray':'4 7'}); }
    make('line',{x1:0,y1:zero,x2:width,y2:zero,stroke:'#64748B','stroke-dasharray':'3 5'});
    make('path',{d:area,fill:primary,opacity:'.08'});
    make('polyline',{points:coords,fill:'none',stroke:primary,'stroke-width':3,'stroke-linejoin':'round','stroke-linecap':'round'});
    make('circle',{cx:x(values.length-1),cy:y(values.at(-1)),r:6,fill:'#e49708'});
  }
})();
