import sys

if "--server" in sys.argv:
    from .server import run_headless
    run_headless()
else:
    from .app import main
    main()
