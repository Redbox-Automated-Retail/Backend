# Kiosk Engine: known issues to fix in a patched engine branch

Bugs in the shipped Kiosk Engine (1.88.0.80) found while running it against this backend. Nothing here is patched in
`src/`; these are candidates for a custom engine branch. Each entry gives a workaround that needs no code change.

## 1. Key press with the simulated card reader crashes the engine

**Symptom.** With `HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Remote Services\Device Service Simulator\UseSimulatedCardReader`
= `True`, pressing a key while the engine is on the start view (it is waiting for a card) ends the engine:

```
ERROR|Unhandled exception occured in Program.Run. Exiting Application
System.NullReferenceException: Object reference not set to an instance of an object.
   at Redbox.KioskEngine.Environment.DeviceServiceClientHelperSimulator.ProcessKey(Char keyChar)
   at Redbox.KioskEngine.Bootstrap.HostForm.OnKeyPress(Object sender, KeyPressEventArgs e)
   at Redbox.KioskEngine.Bootstrap.EngineApplication.Scene_OnWPFKeyUp(Object sender, KeyEventArgs e)
```

It also happens on a kiosk with a real Device Service: the simulator flag alone selects
`DeviceServiceClientHelperSimulator`.

**Cause** (`src/KioskEngine/Redbox.KioskEngine.Environment/DeviceServiceClientHelperSimulator.cs`):

- `ReadConfigFile()` (constructor) loads `_model` from `<DataPath>\KioskEngineSimulatedCardReaderData.json` only if
  the file exists; otherwise `_model` stays `null`.
- `ProcessKey` (line 108) evaluates
  `_readCardJob != null && _readCardJob.IsExecuting && _model.StoredCardData.ContainsKey(...)`: while a card read is
  pending (the start view), `_model` is dereferenced and throws. `HostForm.OnKeyPress` does not catch it, and
  `Program.Run` exits on any unhandled exception.
- The same happens when the file exists but has no `StoredCardData` (`null` dictionary).

**Fix for the branch.**

```csharp
public bool ProcessKey(char keyChar)
{
    var stored = _model?.StoredCardData;
    if (_readCardJob == null || !_readCardJob.IsExecuting || stored == null || !stored.TryGetValue(keyChar.ToString(), out var cardData))
        return false;
    ...
}
```

Also worth doing: log once when the simulator data file is missing, and wrap the key handlers in `HostForm` so that an
exception in a key handler is logged instead of ending the engine.

**Workaround (no code change).** Create `C:\Program Files\Redbox\REDS\Kiosk Engine\data\KioskEngineSimulatedCardReaderData.json`:

```json
{"CurrentCard": null, "StoredCardData": {}}
```

Key presses are then ignored. To swipe test cards from the keyboard, map keys to `CardData` entries in
`StoredCardData`. `tests/clients/sandbox/steps/50-engine.ps1` writes the empty file. Checked in Windows Sandbox: three
key presses on the start view, and the engine kept running. Before, one key press ended it.

## 2. Observations, not necessarily bugs

- **The broker connection drops right after a reservation.** After answering a Reserve3 on the start view, the
  engine resets its bundle services. That resets the BrokerServices kernel extension, whose connection then drops
  mid-stream ("Incorrect incoming stream"). The kiosk normally registers again. When the engine crashed in this
  window (issue 1), it did not. The backend reports the kiosk as lost meanwhile.
- **Reserve3 can take about 30 s right after start-up.** The call waits in the engine's callback queue behind
  startup inventory updates (327 queued). The backend's broker allows 90 s (`BROKER_CALL_TIMEOUT`).
- **`Unhandled job result code: ExcludedSlot passed to sync_inventory!`** is logged by the rental bundle's Lua for
  HAL sync results it does not map.
