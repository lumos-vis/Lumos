# Lumos - Frontend

This folder contains the Lumos frontend code.

## Setup
- Download and install Node.js. We tested it on v22. If you need to run different versions of Node.js in your local environment, consider installing [Node Version Manager (nvm)](https://github.com/creationix/nvm) or [Node Version Manager (nvm) for Windows](https://github.com/coreybutler/nvm-windows).
   - **(using NVM)** see above sections for installing/using _NVM_
     - Once installed, make sure to set Node.js to the correct version for your current session!
   - **(manually)** You can download a Node.js installer for your operating system from <https://nodejs.org/en/download/>
     - Check the version of Node.js that you have installed by running `node --version` from the command line/terminal
     - Be careful of conflicting with existing installations of Node.js on your machine!
   - By installing Node.js, you also get [npm](https://www.npmjs.com/), which is a command line executable for downloading and managing Node.js packages.
- `npm install -g @angular/cli@7` - Install compatible [angular-cli](https://cli.angular.io/)
- `export NODE_OPTIONS=--openssl-legacy-provider` (use `set` instead of `export` for Windows)
- `npm install --legacy-peer-deps` installs all dependencies and devDependencies from package.json

## Run
- Ensure that the **server** application is running in a separate terminal.
- Ensure that the `src/app/models/config.ts` > `DeploymentConfig.SERVER_URL` variable is correctly set to the aforementioned server's URL (http://localhost:3000).
- Execute `ng serve` - compile and serve the application locally
- Open the browser at <http://localhost:4200>
- Enjoy!


## Build and Deployment
- Ensure that the `src/app/models/config.ts` > `DeploymentConfig.SERVER_URL` variable is correctly set to the aforementioned server's URL (https://lumos-webapp-4aeadb3bf30d.herokuapp.com).
- `ng build` - build the app and push the output into [angular.json](angular.json) > `outputPath` directory (default value = ["../server/public/"](../server/public/)). Follow instructions in the [../server/README.md](../server/README.md) for production deployment.
