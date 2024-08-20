# Economies of Scale to Consumption in Collective Households

## Overview

This repository contains Python scripts used for the empirical demonstration of the paper _Economies of Scale to Consumption in Collective Households_ by Hsin-Yuan Hsieh, Arthur Lewbel, and Krishna Pendakur (2024). The data is sourced from the 1997-2009 _Canadian Survey of Household Spending_ public-use microdata.

## Contents

### Scripts

1. **scale_econs.py**
   - **Purpose**: Provides the main estimation results of the empirical demonstration.
   - **Outputs**:
     - Estimates of mean Barten scales and their standard errors.
     - Estimates of Barten scale covariance matrices.
     - Estimate of mean household-level economies of scale index and its standard error.
     - Estimate of the standard deviation of household-level economies of scale index.
     - Tests of model assumptions, identification, and the shareability of goods.

2. **scale_econs_bootstrap_lasso.py**
   - **Purpose**: Provides the bootstrap results of Barten scale covariance matrices (with LASSO).
   - **Outputs**:
     - Bootstrap standard errors of Barten scale covariance matrices.
     - Bootstrap standard errors of Barten scale correlation matrices.
     - Bootstrap standard errors of Barten scale standard deviations.
     - Bootstrap standard error of the standard deviation of household-level economies of scale index.
     - LASSO robustness check.

3. **scale_econs_bootstrap.py**
   - **Purpose**: Provides the bootstrap results of Barten scale standard deviations (without LASSO).
   - **Outputs**:
     - Bootstrap standard errors of Barten scale standard deviations.

## Usage

Each script can be run separately to generate the corresponding results. Ensure that all necessary dependencies are installed and that the data from the 1997-2009 Canadian Survey of Household Spending is properly formatted.

## Acknowledgements

This project makes use of functions from the following repositories:

- [alan-turing-institute/bocpdms](https://github.com/alan-turing-institute/bocpdms.git): Uses the function `nearestPD.NPD` to find the nearest positive definite matrix.

- [3zhang/Python-Lasso-ElasticNet-Ridge-Regression-with-Customized-Penalties](https://github.com/3zhang/Python-Lasso-ElasticNet-Ridge-Regression-with-Customized-Penalties.git): Uses the functions `custom_enet.CustomENetCV` and `custom_enet.CustomENet` to customize the LASSO penalties for different covariates.

## Issues and Contact

For any questions or issues, please open an issue on this repository or contact us at [jeff_hsieh@sfu.ca](mailto:jeff_hsieh@sfu.ca).

---

By utilizing this repository, you agree to acknowledge the authors and source appropriately in any publications or derivative works. 
