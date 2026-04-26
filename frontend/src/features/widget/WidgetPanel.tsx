import { AvatarCanvas } from "../avatar/AvatarCanvas";
import { tanishiStates } from "../avatar/states";
import { useAppStore } from "../state/store";

export function WidgetPanel() {
  const avatar = useAppStore((s) => s.avatar);
  const widget = useAppStore((s) => s.widget);
  const setMode = useAppStore((s) => s.setMode);
  const markRead = useAppStore((s) => s.markRead);
  const current = tanishiStates[avatar.emotionState];

  return (
    <section className="widget-stage">
      <div className="widget">
        <div className="w-head">
          <div className="w-avatar">
            <AvatarCanvas state={avatar.emotionState} speakingLevel={avatar.speakingLevel} />
          </div>
          <div className="w-head-text">
            <div className="w-name-row">
              <div className="w-name">Tanishi</div>
              {widget.unreadCount > 0 ? (
                <span className="w-badge" aria-label={`${widget.unreadCount} unread`}>
                  {widget.unreadCount}
                </span>
              ) : null}
            </div>
            <div className="w-status">● {current.code}</div>
          </div>
        </div>
        <div className="w-preview">
          <div className="w-preview-k">{widget.previewTitle}</div>
          <div className="w-preview-body">{widget.previewBody}</div>
        </div>
        <div className="w-msg">"{current.caption}"</div>
        <div className="w-actions">
          {widget.quickActions.map((action) => {
            if (action === "Reply") {
              return (
                <button
                  key={action}
                  type="button"
                  className="w-action w-action--primary"
                  onClick={() => {
                    void markRead();
                    setMode("stage");
                  }}
                >
                  {action}
                </button>
              );
            }
            return (
              <button
                key={action}
                type="button"
                className="w-action"
                title="Coming soon"
                disabled
              >
                {action}
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
