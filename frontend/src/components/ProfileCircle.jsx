/** Netflix-style round profile avatar. Falls back to the slot's first
 * initial on a solid tile when there's no avatar_url (custom or seeded from
 * Spotify -- see bot/slot_store.py's SlotMetadata.avatar_url). */
export default function ProfileCircle({ name, avatarUrl, size = "lg", onClick, running, busy, children }) {
  const className = `profile-circle profile-circle-${size}${running ? " profile-circle-running" : ""}`;
  return (
    <button type="button" className={className} onClick={onClick} disabled={busy}>
      {avatarUrl ? (
        <img src={avatarUrl} alt="" className="profile-circle-img" />
      ) : (
        <span className="profile-circle-initial">{name ? name[0].toUpperCase() : "?"}</span>
      )}
      {children}
    </button>
  );
}
