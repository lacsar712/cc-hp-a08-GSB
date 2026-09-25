<script>
  let username = 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let who = localStorage.getItem('herb_user') || ''
  let view = 'records'
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let error = ''
  let conflict = false
  let pairs = []
  let events = []
  let pairA = ''
  let pairB = ''
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
    if (!res.ok) {
      const err = new Error(data.detail || '请求失败')
      err.status = res.status
      throw err
    }
    return data
  }

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    who = data.username
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    localStorage.setItem('herb_user', who)
    view = 'records'
    await load()
  }

  async function load() {
    rows = await api('/api/batches')
  }

  async function loadIncompat() {
    ;[pairs, events] = await Promise.all([api('/api/incompat/pairs'), api('/api/incompat/events')])
  }

  function show(next) {
    view = next
    pairError = ''
    if (next === 'incompat') loadIncompat()
  }

  async function save() {
    error = ''
    conflict = false
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
      conflict = err.status === 409
    }
  }

  async function addPair() {
    pairError = ''
    try {
      await api('/api/incompat/pairs', {
        method: 'POST',
        body: JSON.stringify({ herb_a: pairA, herb_b: pairB }),
      })
      pairA = ''
      pairB = ''
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

  function fmt(iso) {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    view = 'records'
  }

  if (token) load()
</script>

<main>
  {#if !token}
    <h1>饮片炮制记录台</h1>
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav>
      <strong>饮片炮制记录台</strong>
      <a href="#" class:active={view === 'records'} on:click|preventDefault={() => show('records')}>炮制记录</a>
      <a href="#" class:active={view === 'incompat'} on:click|preventDefault={() => show('incompat')}>配伍禁忌</a>
      <span class="who">{role === 'writer' ? '炮制员' : '质检员'} · {who}</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'records'}
      {#if role === 'writer'}
        <input bind:value={herb} placeholder="饮片" />
        <input type="number" bind:value={tempC} />
        <input type="number" bind:value={minutes} />
        <button on:click={save}>写入清炒记录</button>
        {#if error}
          <p class="err">
            {error}
            {#if conflict}
              <a href="#" on:click|preventDefault={() => show('incompat')}>前往配伍禁忌页查看</a>
            {/if}
          </p>
        {/if}
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      <h2>配伍禁忌</h2>

      <section>
        <h3>当日冲突提示</h3>
        {#if pairs.length === 0}
          <p>尚未登记禁忌对。</p>
        {:else}
          <ul>
            {#each pairs as p}
              {#if p.a_today && p.b_today}
                <li class="warn">「{p.herb_a}」与「{p.herb_b}」今日均已写入，请核查。</li>
              {:else if p.a_today}
                <li class="warn">今日已有「{p.herb_a}」记录，「{p.herb_b}」禁止写入。</li>
              {:else if p.b_today}
                <li class="warn">今日已有「{p.herb_b}」记录，「{p.herb_a}」禁止写入。</li>
              {:else}
                <li class="ok">「{p.herb_a}」与「{p.herb_b}」今日均未写入，暂不冲突。</li>
              {/if}
            {/each}
          </ul>
        {/if}
      </section>

      <section>
        <h3>禁忌对维护</h3>
        {#if role === 'writer'}
          <input bind:value={pairA} placeholder="甲味" />
          <input bind:value={pairB} placeholder="乙味" />
          <button on:click={addPair}>登记禁忌对</button>
          {#if pairError}<p class="err">{pairError}</p>{/if}
        {:else}
          <p>质检员仅可查看禁忌表与履历，不能增删。</p>
        {/if}
        <table>
          <thead>
            <tr><th>甲味</th><th>乙味</th><th>登记人</th>{#if role === 'writer'}<th>操作</th>{/if}</tr>
          </thead>
          <tbody>
            {#each pairs as p}
              <tr>
                <td>{p.herb_a}</td>
                <td>{p.herb_b}</td>
                <td>{p.created_by}</td>
                {#if role === 'writer'}<td><button on:click={() => removePair(p.id)}>删除</button></td>{/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </section>

      <section>
        <h3>增删履历</h3>
        {#if events.length === 0}
          <p>暂无履历。</p>
        {:else}
          <table>
            <thead>
              <tr><th>时间</th><th>操作</th><th>禁忌对</th><th>操作人</th></tr>
            </thead>
            <tbody>
              {#each events as e}
                <tr>
                  <td>{fmt(e.created_at)}</td>
                  <td>{e.action === 'add' ? '登记' : '删除'}</td>
                  <td>{e.herb_a} × {e.herb_b}</td>
                  <td>{e.actor}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; }
  input { margin-right: 8px; padding: 6px; }
  nav { display: flex; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 2px solid #7c2d12; margin-bottom: 16px; }
  nav strong { color: #7c2d12; }
  nav a { color: #7c2d12; text-decoration: none; padding: 2px 6px; }
  nav a.active { border-bottom: 2px solid #7c2d12; font-weight: bold; }
  nav .who { margin-left: auto; color: #8a6d52; font-size: 14px; }
  .err { color: #b91c1c; }
  .err a { color: #b91c1c; margin-left: 8px; }
  .warn { color: #b91c1c; }
  .ok { color: #15803d; }
  table { border-collapse: collapse; margin-top: 8px; }
  th, td { border: 1px solid #d6c4b2; padding: 6px 12px; text-align: left; }
  section { margin-bottom: 24px; }
</style>
