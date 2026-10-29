# Harbor Studio project estimator

Calculate a project estimate from hours and a customer-supplied hourly rate.

Open index.html in a current browser. Enter values in the agreed ranges; results update as you type. Calculate submits the same computation and Reset restores defaults. No installation, external library, network or account is required.

Files: index.html is the self-contained runnable application; calculator.js and calculator.css are editable source; scope.json records the accepted inputs, outputs and independent acceptance cases.

## Embed safely

Serve index.html from a separate static origin and use an iframe with sandbox="allow-scripts". Do not add allow-same-origin, navigation, form, popup or download permissions. Never serve generated pages in an operator-authenticated application origin. A parent may alternatively use an opaque sandboxed srcdoc frame.

## Numerical limits

This category supports arithmetic calculators over finite numeric inputs, not arbitrary applications, networking, file access, accounts or a financial ledger. Division by zero and non-finite/very large outputs display an explicit error. Outputs use IEEE-754 numbers; it is not exact monetary accounting.

## Agreed requirements

- Calculate the agreed results
- Update results when an input changes
- Allow resetting inputs to their starting values
- Reject invalid or out-of-range inputs
- Work on narrow screens
- Support keyboard entry

## Acceptance cases

- Eight-hour project: {"expected":{"estimate":480},"inputs":{"hours":8,"rate":60},"tolerance":0}
- Fractional hours: {"expected":{"estimate":200},"inputs":{"hours":2.5,"rate":80},"tolerance":1e-06}
- No hours: {"expected":{"estimate":0},"inputs":{"hours":0,"rate":60},"tolerance":0}
