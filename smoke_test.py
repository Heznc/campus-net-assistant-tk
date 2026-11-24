"""冒烟测试：不显示窗口，逐个切换视图，确保没有 Tcl 异常。"""
import sys
import traceback

import tk_app
from engine import Engine


def main() -> int:
    engine = Engine()
    app = tk_app.App(engine, start_hidden=True)
    app.update_idletasks()
    ok = True
    for view in ("overview", "settings", "logs", "about"):
        try:
            app._switch(view)
            app.update_idletasks()
            print(f"view {view}: ok")
        except Exception:
            ok = False
            print(f"view {view}: FAIL")
            traceback.print_exc()
    try:
        app._sync_settings()
        app._render(engine.snapshot())
        app.update_idletasks()
        print("settings sync + render: ok")
    except Exception:
        ok = False
        traceback.print_exc()
    app.engine.shutdown()
    try:
        app.destroy()
    except Exception:
        pass
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
