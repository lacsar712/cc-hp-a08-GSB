<script>
  let username = localStorage.getItem('herb_username') || 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let view = 'batches'
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let error = ''
  let errorIsConflict = false

  let pairs = []
  let history = []
  let today = { day: '', pairs: [] }
  let pairA = '甘草'
  let pairB = '黄芩'
  let pairError = ''

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    username = data.username
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    localStorage.setItem('herb_username', username)
    await load()
  }

  async function load() {
    rows = await api('/api/batches')
    await loadIncompat()
  }

  async function loadIncompat() {
    pairs = await api('/api/incompat/pairs')
    history = await api('/api/incompat/history')
    today = await api('/api/incompat/today')
  }

  async function save() {
    error = ''
    errorIsConflict = false
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await load()
    } catch (err) {
      error = err.message
      errorIsConflict = err.message.includes('配伍禁忌')
    }
  }

  async function addPair() {
    pairError = ''
    try {
      await api('/api/incompat/pairs', {
        method: 'POST',
        body: JSON.stringify({ herb_a: pairA, herb_b: pairB }),
      })
      await loadIncompat()
    } catch (err) {
      pairError = err.message
    }
  }

  async function removePair(id) {
    pairError = ''
    try {
      await api(`/api/incompat/pairs/${id}`, { method: 'DELETE' })
      await loadIncompat()
    } catch (err) {
      pairError = err.message
    }
  }

  function show(v) {
    view = v
    if (v === 'incompat') loadIncompat()
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
  }

  function fmt(iso) {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  }

  function statusText(verdicts) {
    if (!verdicts.length) return '今日未写入'
    return `今日已写入 ${verdicts.length} 条（${verdicts.join('、')}）`
  }

  function hintText(t) {
    const a = t.a_verdicts.length > 0
    const b = t.b_verdicts.length > 0
    if (a && b) return '两味今日均已写入，登记前已存在的记录不受追溯'
    if (a) return `「${t.herb_b}」今日禁写`
    if (b) return `「${t.herb_a}」今日禁写`
    return '两味今日均可写入，先写一味后另一味将被拒'
  }

  if (token) load()
</script>

<main>
  <h1>饮片炮制记录台</h1>
  {#if !token}
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav>
      <button class:active={view === 'batches'} on:click={() => show('batches')}>炮制记录</button>
      <button class:active={view === 'incompat'} on:click={() => show('incompat')}>配伍禁忌</button>
      <span class="who">{username}（{role === 'writer' ? '炮制员' : '质检员'}）</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'batches'}
      {#if role === 'writer'}
        <section>
          <input bind:value={herb} placeholder="饮片" />
          <input type="number" bind:value={tempC} />
          <input type="number" bind:value={minutes} />
          <button on:click={save}>写入清炒记录</button>
          {#if error}
            <p class="err">
              {error}
              {#if errorIsConflict}
                <button class="link" on:click={() => show('incompat')}>前往配伍禁忌页查看</button>
              {/if}
            </p>
          {/if}
        </section>
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      <section>
        <h2>禁忌对维护</h2>
        {#if role === 'writer'}
          <input bind:value={pairA} placeholder="药味甲" />
          <input bind:value={pairB} placeholder="药味乙" />
          <button on:click={addPair}>登记禁忌对</button>
          {#if pairError}<p class="err">{pairError}</p>{/if}
        {:else}
          <p class="muted">质检员仅可查看禁忌表与履历，不能增删。</p>
        {/if}
        <table>
          <thead>
            <tr><th>药味甲</th><th>药味乙</th><th>登记人</th><th>登记时间</th>{#if role === 'writer'}<th>操作</th>{/if}</tr>
          </thead>
          <tbody>
            {#each pairs as p}
              <tr>
                <td>{p.herb_a}</td>
                <td>{p.herb_b}</td>
                <td>{p.created_by}</td>
                <td>{fmt(p.created_at)}</td>
                {#if role === 'writer'}<td><button on:click={() => removePair(p.id)}>删除</button></td>{/if}
              </tr>
            {:else}
              <tr><td colspan="5" class="muted">暂无禁忌对</td></tr>
            {/each}
          </tbody>
        </table>
      </section>

      <section>
        <h2>当日冲突提示（{today.day}）</h2>
        {#each today.pairs as t}
          <p class="hint">
            「{t.herb_a}」{statusText(t.a_verdicts)}；「{t.herb_b}」{statusText(t.b_verdicts)} —— {hintText(t)}
          </p>
        {:else}
          <p class="muted">暂无禁忌对，今日无配伍限制。</p>
        {/each}
      </section>

      <section>
        <h2>增删履历</h2>
        <ul>
          {#each history as h}
            <li>{fmt(h.created_at)} · {h.actor} {h.action === 'add' ? '登记' : '删除'}禁忌对「{h.herb_a} × {h.herb_b}」</li>
          {:else}
            <li class="muted">暂无履历</li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; font-size: 18px; margin-bottom: 8px; }
  input { margin-right: 8px; padding: 6px; }
  nav { display: flex; align-items: center; gap: 8px; border-bottom: 2px solid #7c2d12; padding-bottom: 8px; margin-bottom: 16px; }
  nav .who { margin-left: auto; color: #78716c; }
  nav button.active { background: #7c2d12; color: #fff; }
  section { margin-bottom: 24px; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #d6c9bb; padding: 6px 10px; text-align: left; }
  th { background: #f5ede4; }
  .err { color: #b91c1c; }
  .muted { color: #78716c; }
  .hint { background: #fef3c7; border: 1px solid #f59e0b; padding: 8px 10px; border-radius: 4px; }
  button.link { margin-left: 8px; }
</style>
