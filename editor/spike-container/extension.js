// Throwaway probe for Spike S1. Renders a single line so that "the view
// appeared" is distinguishable from "the container appeared but is empty".
const vscode = require("vscode");

function activate(context) {
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("spike.views.probe", {
      resolveWebviewView(view) {
        view.webview.html =
          "<html><body><p>S1 PROBE RENDERED</p></body></html>";
      },
    })
  );
}

module.exports = { activate, deactivate() {} };
