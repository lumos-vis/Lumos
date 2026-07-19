# Lumos - Frontend

This folder contains the Lumos frontend code.

## Setup

### Mac/Linux 
- Download and install Node.js. We tested it on v22. If you need to run different versions of Node.js in your local environment, consider installing [Node Version Manager (nvm)](https://github.com/creationix/nvm). 
   - **(using NVM)** see above sections for installing/using _NVM_
     - Once installed, make sure to set Node.js to the correct version for your current session!
   - **(manually)** You can download a Node.js installer for your operating system from <https://nodejs.org/en/download/>
     - Check the version of Node.js that you have installed by running `node --version` from the command line/terminal
     - Be careful of conflicting with existing installations of Node.js on your machine!
   - By installing Node.js, you also get [npm](https://www.npmjs.com/), which is a command line executable for downloading and managing Node.js packages.
- `npm install -g @angular/cli@7` - Install compatible [angular-cli](https://cli.angular.io/)
- `export NODE_OPTIONS=--openssl-legacy-provider`.
- `npm install --legacy-peer-deps` installs all dependencies and devDependencies from package.json

### Windows 11

There are several package and version compatibility issues when attempting to run the Lumos project on Windows. The configuration details outlined below were tested specifically on Windows 11.

- Download and install Node.js [Node Version Manager (nvm) for Windows](https://github.com/coreybutler/nvm-windows), and use the version 12.22.12.
- Replace the existing dependencies in your package.json file with the following.
```json
{
  "dependencies": {
    "@types/d3": "6.7.5",
    "@types/d3-scale-chromatic": "2.0.1",
    "@types/jquery": "3.5.14",
    "@types/overlayscrollbars": "1.12.1"
  }
}
```
- Replace the existing devDependencies in your package.json file with the following.
```json
{
  "devDependencies": {
    "@types/bootstrap": "4.6.2",
    "@types/jasmine": "2.8.19",
    "@types/jasminewd2": "2.0.10"
  }
}
```
- Add the following new devDependencies in your package.json file.
```json
{
  "devDependencies": {
    "@types/d3-array": "2.12.3",
    "@types/d3-axis": "2.1.3",
    "@types/d3-brush": "2.1.2",
    "@types/d3-chord": "2.0.3",
    "@types/d3-color": "2.0.3",
    "@types/d3-contour": "2.0.4",
    "@types/d3-delaunay": "5.3.1",
    "@types/d3-dispatch": "2.0.1",
    "@types/d3-drag": "2.0.2",
    "@types/d3-dsv": "2.0.2",
    "@types/d3-ease": "2.0.2",
    "@types/d3-fetch": "2.0.2",
    "@types/d3-force": "2.1.4",
    "@types/d3-format": "2.0.2",
    "@types/d3-geo": "2.0.3",
    "@types/d3-hierarchy": "2.0.2",
    "@types/d3-interpolate": "2.0.2",
    "@types/d3-path": "2.0.2",
    "@types/d3-polygon": "2.0.1",
    "@types/d3-quadtree": "2.0.2",
    "@types/d3-random": "2.2.1",
    "@types/d3-scale": "3.3.2",
    "@types/d3-selection": "2.0.1",
    "@types/d3-shape": "2.1.3",
    "@types/d3-time": "2.1.1",
    "@types/d3-time-format": "3.0.1",
    "@types/d3-timer": "2.0.1",
    "@types/d3-transition": "2.0.2",
    "@types/d3-zoom": "2.0.3"
  } 
}
```
- `npm install -g @angular/cli@7` - Install compatible [angular-cli](https://cli.angular.io/)
- `set NODE_OPTIONS=--openssl-legacy-provider`.
- `npm install` installs all dependencies and devDependencies from package.json


## Run
- Ensure that the **server** application is running in a separate terminal.
- Ensure that the `src/app/models/config.ts` > `DeploymentConfig.SERVER_URL` variable is correctly set to the aforementioned server's URL (http://localhost:3000).
- Execute `ng serve` - compile and serve the application locally
- Open the browser at <http://localhost:4200>
- Enjoy!


## Build and Deployment
- Ensure that the `src/app/models/config.ts` > `DeploymentConfig.SERVER_URL` variable is correctly set to the aforementioned server's URL (https://lumos-webapp-4aeadb3bf30d.herokuapp.com).
- `ng build` - build the app and push the output into [angular.json](angular.json) > `outputPath` directory (default value = ["../server/public/"](../server/public/)). Follow instructions in the [../server/README.md](../server/README.md) for production deployment.
