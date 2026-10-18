export default function AppearanceSettings({ theme, onChange, saving, error }) {
  return <section className="panel appearance-settings" aria-labelledby="appearance-title">
    <h1 id="appearance-title">Settings</h1>
    <fieldset disabled={saving}>
      <legend>Appearance</legend>
      <p>Choose the look of your dashboard. Your choice is saved for the next launch.</p>
      {[['light', 'Paper & Ember', 'Light surfaces with warm ember accents.'], ['dark', 'Dark Bloom', 'Deep burgundy surfaces with ember accents.']].map(([value, label, description]) =>
        <label className={`theme-choice ${theme === value ? 'selected' : ''}`} key={value}>
          <input type="radio" name="appearance" value={value} checked={theme === value} onChange={() => onChange(value)} />
          <span><strong>{label}</strong><small>{description}</small></span>
        </label>)}
    </fieldset>
    <p role="status">{saving ? 'Saving appearance…' : error ? '' : 'Appearance is saved on this computer.'}</p>
    {error && <div role="alert"><p>{error}</p><button className="button" type="button" disabled={saving} onClick={() => onChange(theme)}>Save current appearance</button></div>}
  </section>
}
