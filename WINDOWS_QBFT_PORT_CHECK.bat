@echo off
echo === QBFT RPC ports ===
for %%P in (8545 8546 8547 8548 8549 8550 8551) do (
  echo --- port %%P ---
  netstat -ano | findstr :%%P
)
echo.
echo === QBFT P2P ports ===
for %%P in (30303 30304 30305 30306 30307 30308 30309) do (
  echo --- port %%P ---
  netstat -ano | findstr :%%P
)
echo.
echo === Java processes ===
tasklist | findstr /I java
