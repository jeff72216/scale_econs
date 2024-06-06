"""
          Economies of Scale to Consumption in Collective Households
              Hsin-Yuan Hsieh, Arthur Lewbel, & Krishna Pendakur
                      Bootstrap (clustered, with LASSO)
"""
import os
import pyreadstat
import numpy as np
import pandas as pd

from multiprocessing import Pool
from scipy.sparse import csr_array
from linearmodels.system import SUR
from collections import OrderedDict
from sklearn.model_selection import RepeatedKFold
from tqdm import tqdm

from bocpdms.nearestPD import NPD # Borrowed from https://github.com/alan-turing-institute/bocpdms.git
from custom_enet import CustomENetCV, CustomENet # Borrowed from https://github.com/3zhang/Python-Lasso-ElasticNet-Ridge-Regression-with-Customized-Penalties.git


# Define functions.
def demean(var, gt):
    """Difference-out the group mean from each observation.

    Parameters:
    -----------
    var: 
        An N*P NumPy array of the sample where N is the number of observations  
        and P is the number of variables.
    gt:
        A sparse matrix of group indicators generated by the function 
        scipy.sparse.csr_array(matrix). The input matrix must be an N*G matrix 
        where N is the number of observations and G is the number of groups.

    Outputs:
    --------
    var_demean: 
        An N*P NumPy array of the demeaned sample where N is the number of      
        observations and P is the number of variables.
    """
    var_demean = np.zeros((var.shape[0], var.shape[1]))

    for i in np.unique(gt[1]):
        index = gt[0][np.where(gt[1] == i)]
        var_demean[index, :] = var[index, :] - var[index, :].mean(axis=0)
    
    return var_demean

def cov_to_cor(cov):
    """Convert a covariance matrix to a correlation matrix.

    Parameters:
    -----------
    cov:
        A 2-d NumPy array of covariance matrix.

    Outputs:
    --------
    cor:
        A 2-d NumPy array of correlation matrix.
    """
    v = np.sqrt(np.diag(cov))
    cor = cov / np.outer(v, v)

    return cor

def barten(theta, num_eq):
    """Calculate Barten scale estimates from the estimates of the reduced-form 
    regressions.

    Parameters:
    -----------
    theta:
        A NumPy array of length 2*K, where K is the number of shareable goods. 
        The first K elements of the array must be the reduced-form estimates for 
        single-member households, and the last K elements must be the 
        corresponding estimates for multi-member households.
    num_eq:
        The number of equations in the regression system, aka the number of 
        shareable goods.

    Outputs:
    --------
    a:
        A NumPy array of Barten scale estimates.
    """
    b_s = theta[:num_eq]
    b_h = theta[num_eq:]

    a = b_h / b_s

    return a

def barten_results(mean_est, barten=barten, TYPE=None, NAME_w=None):
    """Generate a dataframe for Barten scale estimates.

    Parameters:
    -----------
    mean_est:
        Estimation results of the reduced-form equations for mean Barten scales. 
        It is genetated directly by the function linearmodels.system.SUR.fit().
    barten:
        A previously defined function that returns Barten scale estimates.
    barten_deriv:
        A previously defined function that returns Barten scale derivatives.
    TYPE:
        A tuple of indices for household types.
    NAME_w:
        A list of indices for shareable goods.

    Outputs:
    --------
    a:
        A dataframe listing all Barten scale estimates, with K rows for 
        shareable goods and T columns for household types.
    """
    a = pd.DataFrame(index=NAME_w, 
                     columns=[f'h{i}' for i in range(len(TYPE))], 
                     dtype=float)

    for i in range(len(TYPE)):
        theta_s = mean_est.params[
            (mean_est.params.index.str.contains('s') == True) & 
            (mean_est.params.index.str.contains('z') == False)
            ]
        theta_h = mean_est.params[
            (mean_est.params.index.str.contains(f'h{i+1}') == True) & 
            (mean_est.params.index.str.contains('z') == False)
            ]
        theta = np.concatenate([theta_s.values, theta_h.values])
        
        a[f'h{i}'] = barten(theta, len(NAME_w))
        
    return a

def scale(barten, cov, data, single_indicator, TYPE, NAME_w):
    """Generate the estimated standard deviations of the random household-level 
    scale economies index.

    Parameters:
    -----------
    barten:
        A dataframe of Barten scale estimates under different household types. 
        This is the output of the function barten_results().
    cov:
        A 3-d NumPy array of the of the estimated random Barten scale covariance 
        matrices (the second moment of Barten scales) under different household 
        types, with dimension (K, K, T), where K is the number of shareable 
        goods and T is the number of household types.
    data:
        An N*(K+1) NumPy array of the budget shares where N is the number of 
        observations and K is the number of shareable goods. The last column 
        should be the nonshareable good.
    single_indicator:
        A NumPy array of length N indicating if an observation is single, where 
        N is the number of observations.
    TYPE:
        A tuple of indices for household types.
    NAME_w:
        A list of indices for shareable goods.

    Outputs:
    --------
    std:
        A NumPy array of the estimated standard deviations of the random 
        household-level scale economies index under different household types.
    """
    w_bar = data[single_indicator == 1, :].mean(axis=0)
    w_cov = pd.DataFrame(data[single_indicator == 1, :]).cov().values
    std = []
    
    for i in range(len(TYPE)):
        barten_full = np.concatenate([barten.iloc[:, i], [1]])
        barten_cov_full = np.block(
            [[cov[:, :, i], np.zeros((len(NAME_w), 1))], 
             [np.zeros((1, len(NAME_w) + 1))]]
            )
        std.append(np.sqrt(barten_full@w_cov@barten_full 
                           + w_bar@barten_cov_full@w_bar))
        
    return np.array(std)

def bootstrap(
        resampling, 
        shs_data, 
        lasso_selec, 
        lasso_l, 
        lasso_u, 
        demean=demean, 
        cov_to_cor=cov_to_cor, 
        TYPE=(24, 27, 28), 
        SHAREABLE=(1, 2, 8, 17, 19), 
        NONSHAREABLE=6, 
        DEMOG=(1, 2)):
    # Generate a bootstrap sample.
    shs_data = pd.DataFrame(
        shs_data, 
        columns=([f's{i}' for i in SHAREABLE] 
                 + [f's{NONSHAREABLE}'] 
                 + [f'p{i}' for i in SHAREABLE] 
                 + [f'p{NONSHAREABLE}'] 
                 + ['x'] 
                 + [f'z{i}' for i in DEMOG] 
                 + [f'z{i}' for i in (3, 23)] 
                 + [f'z{i}' for i in TYPE] 
                 + [f'z{i}' for i in range(29, 51)])
        )
    shs_data = shs_data.loc[resampling]


    # Setup groups (province-year-type).
    g = pd.DataFrame(
        (shs_data[
            [f'z{i}' for i in range(38, 51)]
            ].values[:, None] 
         * shs_data[
             [f'z{j}' for j in range(29, 38)]
             ].values[..., None]).reshape(shs_data.shape[0], -1)
        )
    NAME_g = [f'g{i+1}' for i in range(np.shape(g)[1])]
    g.columns = NAME_g

    sm = np.where((shs_data['z23'] == 1) & (shs_data['z3'] == 0), 1, 0)
    sf = np.where((shs_data['z23'] == 1) & (shs_data['z3'] == 1), 1, 0)
    t = np.vstack((sm, sf)).T

    for i in TYPE:
        t = np.hstack((t, np.where(shs_data[f'z{i}'] == 1, 1, 0)[:, None]))

    gt = pd.DataFrame(
        (g.values[:, None] * t[..., None]).reshape(shs_data.shape[0], -1), 
        index = shs_data.index
        )
    gt_excluded = [
        i for i in range(gt.shape[1]) if len(
            np.unique(gt[i][gt[i] == 1].index)
            ) == 1
        ]
    gt = gt.reset_index(drop=True)
    idx_excluded = sum(
        [gt.index[gt[i] == 1].tolist() for i in gt_excluded], []
        )
    
    shs_data = (
        shs_data.reset_index(drop=True)
        ).drop(idx_excluded).reset_index(drop=True)
    g = g.drop(idx_excluded).reset_index(drop=True)
    t = np.delete(t, idx_excluded, 0)
    NAME_t = ['sm', 'sf'] + [f'h{i+1}' for i in range(len(TYPE))]
    t_single = np.hstack(((t[:, 0]+t[:, 1])[:, None], t[:, 2:]))
    NAME_t_single = ['s'] + [f'h{i+1}' for i in range(len(TYPE))]

    gt = csr_array(
        gt.drop(idx_excluded).reset_index(drop=True).values
        ).nonzero()
    

    # Create variables.
    y = np.log(shs_data['x'].values) - shs_data[f'p{NONSHAREABLE}'].values
    r = ((shs_data[[f'p{i}' for i in SHAREABLE]]).values 
         - shs_data[f'p{NONSHAREABLE}'].values[:, None])
    er = np.exp(r)
    z = shs_data[[f'z{i}' for i in DEMOG]].values
    w = ((shs_data[[f's{i}' for i in SHAREABLE]]).values 
         / shs_data['x'].values[:, None])
    w_non = shs_data[f's{NONSHAREABLE}'].values / shs_data['x'].values
    NAME_w = [f'w{i}' for i in SHAREABLE]
    wyz = np.hstack((w, y[:, None], z))
    wyz_demean = demean(wyz, gt)
    ery = er * wyz_demean[:, len(SHAREABLE)][:, None]


    # Group variables by types.
    eryt = pd.DataFrame(
        (ery[:, None] * t_single[..., None]).reshape(t_single.shape[0], -1), 
        columns=[f'er{SHAREABLE[i]}y_{tt}'
                 for tt in NAME_t_single 
                 for i in range(len(SHAREABLE))]
        )
    NAME_eryt = eryt.columns.tolist()
    zt = pd.DataFrame(
        (wyz_demean[:, (len(SHAREABLE)+1):][:, None]
         * t[..., None]).reshape(t.shape[0], -1), 
         columns=[f'z{DEMOG[i]}_{tt}'
                  for tt in NAME_t 
                  for i in range(len(DEMOG))]
        )
    NAME_zt = zt.columns.tolist()


    # Combine variables into a single dataframe.
    shs_data = pd.concat([
        pd.DataFrame(
            wyz_demean[:, range(len(SHAREABLE))], 
            columns = NAME_w
            ), 
        eryt, 
        zt
        ], axis=1)
    w_all = np.hstack((w, w_non[:, None]))


    # Estimate reduced-form equations (mean Barten scales).
    mean_boot_eqs = OrderedDict()

    for i in range(len(SHAREABLE)):
        dep = shs_data[NAME_w[i]]
        exog = shs_data[
            [col for col in NAME_eryt if f"er{SHAREABLE[i]}y_" in col] 
            + NAME_zt
            ]
        mean_boot_eqs[f'eq{i+1}'] = {"dependent":dep, "exog":exog}

    mean_boot_est = SUR(mean_boot_eqs).fit(cov_type="unadjusted")


    # Prepare data for the estimation of Barten scale variances.
    ery = er * y[:, None]
    eryt = (ery[:,None] * t_single[...,None]).reshape(t_single.shape[0], -1)
    zt = (z[:,None] * t[...,None]).reshape(t.shape[0], -1)
    res = np.zeros((np.shape(shs_data)[0], len(SHAREABLE)))

    for i in range(len(SHAREABLE)):
        fres = w[:, i] - (
            np.hstack(
                (eryt[:, np.arange(i, len(NAME_eryt), len(SHAREABLE))], zt)
                )
            @ mean_boot_est.params.filter(like=f'eq{i+1}').values[:, None]
            ).T.flatten()
        for j in np.unique(gt[1]):
            index = gt[0][np.where(gt[1] == j)]
            res[index, i] = fres[index] - np.mean(fres[index])

    bery = ery * mean_boot_est.params.filter(like=f'y_s').values
    bery_crossprod = pd.DataFrame(index=shs_data.index)
    bery_crosssum = pd.DataFrame(index=shs_data.index)
    res_crossprod = pd.DataFrame(index=shs_data.index)

    for i in range(len(SHAREABLE)):
        for j in range(i, len(SHAREABLE)):
            res_crossprod = pd.concat(
                [res_crossprod, 
                 pd.DataFrame(
                     res[:, i] * res[:, j], 
                     columns=[f'res{SHAREABLE[i]}.{SHAREABLE[j]}']
                     )], 
                axis=1
                )
            bery_crossprod = pd.concat(
                [bery_crossprod, 
                 pd.DataFrame(
                     bery[:, i] * bery[:, j], 
                     columns=[f'ber{SHAREABLE[i]}.{SHAREABLE[j]}y_prod']
                     )], 
                axis=1
                )
            bery_crosssum = pd.concat(
                [bery_crosssum, 
                 pd.DataFrame(
                     bery[:, i] + bery[:, j], 
                     columns=[f'ber{SHAREABLE[i]}.{SHAREABLE[j]}y_sum']
                     )], 
                axis=1
                )
        
    NAME_res_crossprod = res_crossprod.columns
    NAME_bery_crossprod = bery_crossprod.columns
    NAME_bery_crosssum = bery_crosssum.columns

    shs_boot_res = pd.concat([
        pd.DataFrame(t_single[:, 1:], columns=NAME_t[2:]), 
        res_crossprod, 
        bery_crossprod, 
        g, 
        pd.DataFrame(
            (bery_crosssum.values[:,None]
             * g.values[...,None]).reshape(g.shape[0], -1),
            columns=[f'g{i+1}_{NAME_bery_crosssum[j]}'
                     for i in range(np.shape(g)[1])
                     for j in range(len(NAME_bery_crosssum))]
            )
        ], 
        axis=1)[~(t_single[:, 0] == 1)].reset_index(drop=True)


    # Estimate Barten scale variances, standard deviations, and correlations.
    cov_matrix_pd = np.empty((len(SHAREABLE), len(SHAREABLE), len(TYPE)))
    cov_matrix_pd_l = np.empty((len(SHAREABLE), len(SHAREABLE), len(TYPE)))
    cov_matrix_pd_u = np.empty((len(SHAREABLE), len(SHAREABLE), len(TYPE)))

    for tt in range(len(TYPE)):
        res_boot = shs_boot_res[shs_boot_res[f'h{tt+1}'] == 1].astype(float)
        g_included_boot = [i for i in NAME_g if res_boot[i].sum() >= 2]
        g_bery_included_boot = [
            f'{i}_{j}' for i in g_included_boot for j in NAME_bery_crosssum
            ]
        g_new_boot = csr_array(res_boot[NAME_g].values)
        g_new_boot = g_new_boot.nonzero()
        res_boot = pd.DataFrame(demean(res_boot.values, g_new_boot), 
                                columns=res_boot.columns)
        var_eqs_boot = OrderedDict()
        var_eqs_l_boot = OrderedDict()
        var_eqs_u_boot = OrderedDict()
        for i in range(len(NAME_res_crossprod)):
            dep = res_boot[NAME_res_crossprod[i]]
            name = NAME_res_crossprod[i].replace("res", "ber") + 'y'
            exog = res_boot[[j for j in g_bery_included_boot if name in j] 
                            + [NAME_bery_crossprod[i]]]
            
            var_eqs_boot[f'eq{i+1}'] = {
                "dependent": dep, 
                "exog": exog[
                    [col for col in lasso_selec[tt*len(NAME_res_crossprod)+i]
                     if col in exog.columns.tolist()]
                    ]
                }
            var_eqs_l_boot[f'eq{i+1}'] = {
                "dependent": dep, 
                "exog": exog[
                    [col for col in lasso_l[tt*len(NAME_res_crossprod)+i]
                     if col in exog.columns.tolist()]
                    ]
                }
            var_eqs_u_boot[f'eq{i+1}'] = {
                "dependent": dep, 
                "exog": exog[
                    [col for col in lasso_u[tt*len(NAME_res_crossprod)+i]
                     if col in exog.columns.tolist()]
                    ]
                }

        cov_est_boot = SUR(var_eqs_boot).fit(cov_type="unadjusted")
        cov_est_l_boot = SUR(var_eqs_l_boot).fit(cov_type="unadjusted")
        cov_est_u_boot = SUR(var_eqs_u_boot).fit(cov_type="unadjusted")
        
        cov_val_new = cov_est_boot.params[
            cov_est_boot.params.index.str.contains("prod")
            ]
        cov_val_l = cov_est_l_boot.params[
            cov_est_l_boot.params.index.str.contains("prod")
            ]
        cov_val_u = cov_est_u_boot.params[
            cov_est_u_boot.params.index.str.contains("prod")
            ]
        
        cov_val_new.index = cov_val_new.index + f'_h{tt+1}'

        cov_matrix = np.empty((len(SHAREABLE), len(SHAREABLE)))
        cov_matrix[np.triu_indices_from(cov_matrix, k=0)] = cov_val_new
        cov_matrix[np.tril_indices_from(cov_matrix, k=-1)] = cov_matrix.T[
            np.tril_indices_from(cov_matrix, k=-1)
            ]

        cov_l = np.empty((len(SHAREABLE), len(SHAREABLE)))
        cov_l[np.triu_indices_from(cov_l, k=0)] = cov_val_l
        cov_l[np.tril_indices_from(cov_l, k=-1)] = cov_l.T[
            np.tril_indices_from(cov_l, k=-1)
            ]

        cov_u = np.empty((len(SHAREABLE), len(SHAREABLE)))
        cov_u[np.triu_indices_from(cov_u, k=0)] = cov_val_u
        cov_u[np.tril_indices_from(cov_u, k=-1)] = cov_u.T[
            np.tril_indices_from(cov_u, k=-1)
            ]

        cov_matrix_pd[:, :, tt] = NPD.nearestPD(cov_matrix)
        cov_matrix_pd_l[:, :, tt] = NPD.nearestPD(cov_l)
        cov_matrix_pd_u[:, :, tt] = NPD.nearestPD(cov_u)
        cor_matrix = cov_to_cor(cov_matrix_pd[:, :, tt])

        cov_pd_new = pd.Series(
            cov_matrix_pd[:, :, tt][
                np.triu_indices_from(cov_matrix_pd[:, :, tt], k=0)
                ]
            )
        cor_new = pd.Series(
            cor_matrix[np.triu_indices_from(cor_matrix, k=0)]
            )
        stdev_new = pd.Series(
            np.sqrt(np.diag(cov_matrix_pd[:, :, tt]))
            )
        stdev_new_l = pd.Series(
            np.sqrt(np.diag(cov_matrix_pd_l[:, :, tt]))
            )
        stdev_new_u = pd.Series(
            np.sqrt(np.diag(cov_matrix_pd_u[:, :, tt]))
            )

        cov = (cov_val_new.copy() if tt==0 else pd.concat(
            [cov, cov_val_new]
            ))
        cov_pd = (cov_pd_new.copy() if tt==0 else pd.concat(
            [cov_pd, cov_pd_new]
            ))
        cor = (cor_new.copy() if tt==0 else pd.concat(
            [cor, cor_new]
            ))
        std = (stdev_new.copy() if tt==0 else pd.concat(
            [std, stdev_new]
            ))
        std_l = (stdev_new_l.copy() if tt==0 else pd.concat(
            [std_l, stdev_new_l]
            ))
        std_u = (stdev_new_u.copy() if tt==0 else pd.concat(
            [std_u, stdev_new_u]
            ))

    # Estimate standard deviation of the scale economies index.
    std_scale_s = scale(
        barten_results(mean_boot_est, TYPE=TYPE, NAME_w=NAME_w), 
        cov_matrix_pd, 
        w_all, 
        t_single[:, 0], 
        TYPE, 
        NAME_w)
    std_scale_sm = scale(
        barten_results(mean_boot_est, TYPE=TYPE, NAME_w=NAME_w), 
        cov_matrix_pd, 
        w_all, 
        t[:, 0], 
        TYPE, 
        NAME_w)
    std_scale_sf = scale(
        barten_results(mean_boot_est, TYPE=TYPE, NAME_w=NAME_w), 
        cov_matrix_pd, 
        w_all, 
        t[:, 1], 
        TYPE, 
        NAME_w)

    return (cov, cov_pd, cor, std, std_scale_s, std_scale_sm, 
            std_scale_sf, std_l, std_u)

def bootstrap_star(args):
    return bootstrap(*args)


# Computation starts.
if __name__ == '__main__':
    # Define parameters.
    AGE = 65 # Age limit
    BOUND = (0.05, 0.90) # (lower, upper) Bounds of expenditure percentile
    TYPE = (24, 27, 28) # Household types
    NUM_PEOPLE = (2, 3, 4) # Number of people in a household
    SHAREABLE = (1, 2, 8, 17, 19) # Shareable goods
    NONSHAREABLE = 6 # Nonshareable good
    DEMOG = (1, 2) # Demographic variables
    rep = 1000 # Number of replications in bootstrap
    np.random.seed(123)


    # Read and filter raw data, exclude observations:
    # 1. in cities under 100k population (z21),
    # 2. in rural area (z22), 
    # 3. above the age limit (z51).
    os.chdir("/Users/Apple/Desktop/Research/econs_scale/data/Canada_SHS/97-09")
    shs_data, meta = pyreadstat.read_dta("SHS97to09_tenants_new.dta")
    shs_data = shs_data[
        ((shs_data[['z23'] + [f'z{idx}' for idx in TYPE]].sum(axis=1) == 1) & 
         (shs_data[['z21', 'z22']].sum(axis=1) == 0) & 
         (shs_data['z51'] <= AGE))
        ]
    

    # Remove observations with missing values.
    for i in SHAREABLE:
        shs_data = shs_data[(shs_data[f's{i}'] != 0)]
    shs_data = shs_data[(shs_data[f's{NONSHAREABLE}'] != 0)]


    # Exclude observations outside the bounds of expenditure percentile.
    shs_data['x'] = shs_data[[f's{i}' for i in SHAREABLE] 
                             + [f's{NONSHAREABLE}']].sum(axis=1)
    selected = (
        (shs_data['x'] > np.quantile(shs_data['x'], BOUND[0])) &
        (shs_data['x'] < np.quantile(shs_data['x'], BOUND[1]))
        )
    shs_data = shs_data[selected].reset_index(drop=True)


    # Generate a new sample for bootstrap.
    shs_boot_cols = ([f's{i}' for i in SHAREABLE] 
                     + [f's{NONSHAREABLE}'] 
                     + [f'p{i}' for i in SHAREABLE] 
                     + [f'p{NONSHAREABLE}'] 
                     + ['x'] 
                     + [f'z{i}' for i in DEMOG] 
                     + [f'z{i}' for i in (3, 23)] 
                     + [f'z{i}' for i in TYPE] 
                     + [f'z{i}' for i in range(29, 51)])
    shs_boot = shs_data[shs_boot_cols].to_numpy()


    # Setup groups (province-year-type).
    g = pd.DataFrame(
        (shs_data[
            [f'z{i}' for i in range(38, 51)]
            ].values[:, None] 
         * shs_data[
             [f'z{j}' for j in range(29, 38)]
             ].values[..., None]).reshape(shs_data.shape[0], -1)
        )
    NAME_g = [f'g{i+1}' for i in range(np.shape(g)[1])]
    g.columns = NAME_g

    clusters = [
        int(
            g.columns[g.iloc[i].isin([1.0])][0].replace('g', '')
            ) for i in range(g.shape[0])
        ]

    sm = np.where((shs_data['z23'] == 1) & (shs_data['z3'] == 0), 1, 0)
    sf = np.where((shs_data['z23'] == 1) & (shs_data['z3'] == 1), 1, 0)
    t = np.vstack((sm, sf)).T

    for i in TYPE:
        t = np.hstack((t, np.where(shs_data[f'z{i}'] == 1, 1, 0)[:, None]))

    gt = pd.DataFrame(
        (g.values[:, None]*t[..., None]).reshape(shs_data.shape[0], -1)
        )
    gt_excluded = [i for i in range(gt.shape[1]) if gt[i].sum() == 1]
    idx_excluded = [
        gt.index[
            gt[gt_excluded[i]] == 1
            ].tolist()[0] for i in range(len(gt_excluded))
            ]

    shs_data = shs_data.drop(idx_excluded).reset_index(drop=True)
    g = g.drop(idx_excluded).reset_index(drop=True)
    t = np.delete(t, idx_excluded, 0)
    NAME_t = ['sm', 'sf'] + [f'h{i+1}' for i in range(len(TYPE))]
    t_single = np.hstack(((t[:, 0] + t[:, 1])[:, None], t[:, 2:]))
    NAME_t_single = ['s'] + [f'h{i+1}' for i in range(len(TYPE))]

    gt = csr_array(
        gt.drop(idx_excluded).reset_index(drop=True).values
        ).nonzero()
    

    # Create variables.
    y = np.log(shs_data['x'].values) - shs_data[f'p{NONSHAREABLE}'].values
    r = ((shs_data[[f'p{i}' for i in SHAREABLE]]).values
         - shs_data[f'p{NONSHAREABLE}'].values[:, None])
    er = np.exp(r)
    z = shs_data[[f'z{i}' for i in DEMOG]].values
    w = ((shs_data[[f's{i}' for i in SHAREABLE]]).values
         / shs_data['x'].values[:, None])
    w_non = shs_data[f's{NONSHAREABLE}'].values / shs_data['x'].values
    NAME_w = [f'w{i}' for i in SHAREABLE]
    wyz = np.hstack((w, y[:, None], z))
    wyz_demean = demean(wyz, gt)
    ery = er * wyz_demean[:, len(SHAREABLE)][:, None]


    # Group variables by types.
    eryt = pd.DataFrame(
        (ery[:, None] * t_single[..., None]).reshape(t_single.shape[0], -1), 
        columns=[f'er{SHAREABLE[i]}y_{tt}'
                 for tt in NAME_t_single 
                 for i in range(len(SHAREABLE))]
        )
    NAME_eryt = eryt.columns.tolist()
    eryt_test = pd.DataFrame(
        (ery[:, None] * t[..., None]).reshape(t.shape[0], -1), 
        columns=[f'er{SHAREABLE[i]}y_{tt}_test'
                 for tt in NAME_t 
                 for i in range(len(SHAREABLE))]
        )
    NAME_eryt_test = eryt_test.columns.tolist()
    zt = pd.DataFrame(
        (wyz_demean[:, (len(SHAREABLE)+1):][:, None]
         * t[..., None]).reshape(t.shape[0], -1), 
         columns=[f'z{DEMOG[i]}_{tt}'
                  for tt in NAME_t 
                  for i in range(len(DEMOG))]
        )
    NAME_zt = zt.columns.tolist()


    # Combine variables into a single dataframe.
    shs_data = pd.concat([
        pd.DataFrame(
            wyz_demean[:, range(len(SHAREABLE))], 
            columns = NAME_w
            ), 
        eryt, 
        eryt_test, 
        zt
        ], axis=1)
    w_all = np.hstack((w, w_non[:, None]))

    del ery, eryt, eryt_test, wyz_demean, w_non, sm, sf, zt, selected


    # Estimate reduced-form equations (mean Barten scales).
    mean_eqs = OrderedDict()

    for i in range(len(SHAREABLE)):
        dep = shs_data[NAME_w[i]]
        exog = shs_data[
            [col for col in NAME_eryt if f"er{SHAREABLE[i]}y_" in col] 
            + NAME_zt
            ]
        mean_eqs[f'eq{i+1}'] = {"dependent": dep, "exog": exog}

    mean_est = SUR(mean_eqs).fit(cov_type="unadjusted")

    del mean_eqs, dep, exog


    # Prepare data for the estimation of Barten scale variances.
    ery = er * y[:, None]
    eryt = (ery[:,None] * t_single[...,None]).reshape(t_single.shape[0], -1)
    zt = (z[:,None] * t[...,None]).reshape(t.shape[0], -1)
    res = np.zeros((np.shape(shs_data)[0], len(SHAREABLE)))

    for i in range(len(SHAREABLE)):
        fres = w[:, i] - (
            np.hstack(
                (eryt[:, np.arange(i, len(NAME_eryt), len(SHAREABLE))], zt)
                ) @ mean_est.params.filter(like=f'eq{i+1}').values[:, None]
            ).T.flatten()
        for j in np.unique(gt[1]):
            index = gt[0][np.where(gt[1] == j)]
            res[index, i] = fres[index] - np.mean(fres[index])

    bery = ery * mean_est.params.filter(like=f'y_s').values
    bery_crossprod = pd.DataFrame(index=shs_data.index)
    bery_crosssum = pd.DataFrame(index=shs_data.index)
    res_crossprod = pd.DataFrame(index=shs_data.index)

    for i in range(len(SHAREABLE)):
        for j in range(i, len(SHAREABLE)):
            res_crossprod = pd.concat(
                [res_crossprod, 
                 pd.DataFrame(
                     res[:, i] * res[:, j], 
                     columns=[f'res{SHAREABLE[i]}.{SHAREABLE[j]}']
                     )], 
                axis=1
                )
            bery_crossprod = pd.concat(
                [bery_crossprod, 
                 pd.DataFrame(
                     bery[:, i] * bery[:, j], 
                     columns=[f'ber{SHAREABLE[i]}.{SHAREABLE[j]}y_prod']
                     )], 
                axis=1
                )
            bery_crosssum = pd.concat(
                [bery_crosssum, 
                 pd.DataFrame(
                     bery[:, i] + bery[:, j], 
                     columns=[f'ber{SHAREABLE[i]}.{SHAREABLE[j]}y_sum']
                     )], 
                axis=1
                )
        
    NAME_res_crossprod = res_crossprod.columns
    NAME_bery_crossprod = bery_crossprod.columns
    NAME_bery_crosssum = bery_crosssum.columns

    shs_res = pd.concat([
        pd.DataFrame(t_single[:, 1:], columns=NAME_t[2:]), 
        res_crossprod, 
        bery_crossprod, 
        g, 
        pd.DataFrame(
            (bery_crosssum.values[:,None]
             * g.values[...,None]).reshape(g.shape[0], -1),
            columns=[f'g{i+1}_{NAME_bery_crosssum[j]}'
                     for i in range(np.shape(g)[1])
                     for j in range(len(NAME_bery_crosssum))]
            )
        ], 
        axis=1)[~(t_single[:, 0] == 1)].reset_index(drop=True)

    del w, y, er, ery, res, z, zt, fres, index, r, eryt, bery, g
    del res_crossprod, bery_crossprod, bery_crosssum
    
    
    # Run LASSO to select variables.
    lasso_selec = []
    lasso_l = []
    lasso_u = []

    for tt in range(len(TYPE)):
        res_data = shs_res[
            shs_res[f'h{tt+1}'] == 1
            ].astype(float).reset_index(drop=True)
        
        g_included = [i for i in NAME_g if res_data[i].sum() >= 2]
        g_bery_included = [
            f'{i}_{j}' for i in g_included for j in NAME_bery_crosssum
            ]
        g_new = csr_array(res_data[NAME_g].values)
        g_new = g_new.nonzero()
        res_data = pd.DataFrame(demean(res_data.values, g_new), 
                                columns=res_data.columns)

        for i in range(len(NAME_res_crossprod)):
            dep = res_data[NAME_res_crossprod[i]]
            name = NAME_res_crossprod[i].replace("res", "ber") + 'y'
            exog = res_data[[j for j in g_bery_included if name in j] 
                            + [NAME_bery_crossprod[i]]]
            cv = RepeatedKFold(random_state=123)
            penalty = np.array([1.0]*(exog.shape[1]-1) + [0.0])
            
            lasso_model = CustomENetCV(
                cv, 
                l1_ratio=1, 
                fit_intercept=False, 
                verbose=False, 
                max_iter=10000)
            lasso_model.fit(exog.to_numpy(), dep.to_numpy(), s=penalty)

            lasso_model_l = CustomENet(
                lasso_model.alpha_best*0.5, 
                l1_ratio=1, 
                fit_intercept=False)
            lasso_model_l.fit(exog.to_numpy(), dep.to_numpy(), s=penalty)

            lasso_model_u = CustomENet(
                lasso_model.alpha_best*2, 
                l1_ratio=1, 
                fit_intercept=False)
            lasso_model_u.fit(exog.to_numpy(), dep.to_numpy(), s=penalty)

            lasso_selec.append(
                exog.columns[np.nonzero(lasso_model.w)[0]].tolist()
                )
            lasso_l.append(
                exog.columns[np.nonzero(lasso_model_l.w)[0]].tolist()
                )
            lasso_u.append(
                exog.columns[np.nonzero(lasso_model_u.w)[0]].tolist()
                )


    # Generate an 2-d array of indices for the resampling process.
    resampling = np.random.choice(
        range(clusters.index(list(set(clusters))[1])), 
        size=(rep, clusters.index(list(set(clusters))[1]))
        )
    for i in range(1, len(list(set(clusters)))-1):
        resampling_new = np.random.choice(
            range(clusters.index(list(set(clusters))[i]),
                  clusters.index(list(set(clusters))[i+1])), 
            size=(
                rep, 
                (clusters.index(list(set(clusters))[i+1])
                 -clusters.index(list(set(clusters))[i]))
                )
            )
        resampling = np.append(resampling, resampling_new, axis=1)
    resampling_new = np.random.choice(
        range(clusters.index(list(set(clusters))[len(list(set(clusters)))-1]), 
              shs_boot.shape[0]), 
        size=(
            rep, 
            (shs_boot.shape[0]
             -clusters.index(list(set(clusters))[len(list(set(clusters)))-1]))
            )
        )
    resampling = np.append(resampling, resampling_new, axis=1)


    # Bootstrap starts, use parallel process.
    args = [(resampling[iter], 
             shs_boot, 
             lasso_selec, 
             lasso_l, 
             lasso_u) for iter in range(rep)]
    pool = Pool()
    results_store = list(tqdm(pool.imap(bootstrap_star, args), total=rep))
    pool.close()
    pool.join()


    # Translate the output of the bootstrap function into variables.
    cov_store = np.zeros(
        (rep, int((len(SHAREABLE)*(len(SHAREABLE)+1)/2)*len(TYPE)))
        )
    cov_pd_store = np.zeros(
        (rep, int((len(SHAREABLE)*(len(SHAREABLE)+1)/2)*len(TYPE)))
        )
    cor_store = np.zeros(
        (rep, int((len(SHAREABLE)*(len(SHAREABLE)+1)/2)*len(TYPE)))
        )
    std_store = np.zeros((rep, int(len(SHAREABLE)*len(TYPE))))
    std_scale_s_store = np.zeros((rep, int(len(TYPE))))
    std_scale_sm_store = np.zeros((rep, int(len(TYPE))))
    std_scale_sf_store = np.zeros((rep, int(len(TYPE))))
    std_l_store = np.zeros((rep, int(len(SHAREABLE)*len(TYPE))))
    std_u_store = np.zeros((rep, int(len(SHAREABLE)*len(TYPE))))

    for i in range(rep):
        cov_store[i, :] = results_store[i][0].to_numpy()
        cov_pd_store[i, :] = results_store[i][1].to_numpy()
        cor_store[i, :] = results_store[i][2].to_numpy()
        std_store[i, :] = results_store[i][3].to_numpy()
        std_scale_s_store[i, :] = results_store[i][4]
        std_scale_sm_store[i, :] = results_store[i][5]
        std_scale_sf_store[i, :] = results_store[i][6]
        std_l_store[i, :] = results_store[i][7].to_numpy()
        std_u_store[i, :] = results_store[i][8].to_numpy()

    
    # Calculate bootstrap standard errors.
    cov_se = np.std(cov_store, axis=0)
    cov_pd_se = np.std(cov_pd_store, axis=0)
    cor_se = np.std(cor_store, axis=0)

    cov_matrix_se = np.zeros((len(SHAREABLE), len(SHAREABLE), len(TYPE)))
    cov_matrix_pd_se = np.zeros((len(SHAREABLE), len(SHAREABLE), len(TYPE)))
    cor_matrix_se = np.zeros((len(SHAREABLE), len(SHAREABLE), len(TYPE)))

    for i in range(len(TYPE)):
        idx = results_store[0][0].index.str.contains(f"h{i+1}")

        cov_matrix_se[:, :, i][
            np.triu_indices_from(cov_matrix_se[:, :, i], k=0)
            ] = cov_se[idx]
        cov_matrix_se[:, :, i][
            np.tril_indices_from(cov_matrix_se[:, :, i], k=-1)
            ] = cov_matrix_se[:, :, i].T[
                np.tril_indices_from(cov_matrix_se[:, :, i], k=-1)
                ]
        
        cov_matrix_pd_se[:, :, i][
            np.triu_indices_from(cov_matrix_pd_se[:, :, i], k=0)
            ] = cov_pd_se[idx]
        cov_matrix_pd_se[:, :, i][
            np.tril_indices_from(cov_matrix_pd_se[:, :, i], k=-1)
            ] = cov_matrix_pd_se[:, :, i].T[
                np.tril_indices_from(cov_matrix_pd_se[:, :, i], k=-1)
                ]

        cor_matrix_se[:, :, i][
            np.triu_indices_from(cor_matrix_se[:, :, i], k=0)
            ] = cor_se[idx]
        cor_matrix_se[:, :, i][
            np.tril_indices_from(cor_matrix_se[:, :, i], k=-1)
            ] = cor_matrix_se[:, :, i].T[
                np.tril_indices_from(cor_matrix_se[:, :, i], k=-1)
                ]

    std_se_df = pd.DataFrame(
        np.std(std_store, axis=0).reshape([len(TYPE), len(SHAREABLE)]).T, 
        index=NAME_w, 
        columns=[f'std.se.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )
    std_se_l_df = pd.DataFrame(
        np.std(std_l_store, axis=0).reshape([len(TYPE), len(SHAREABLE)]).T, 
        index=NAME_w, 
        columns=[f'std.se.lower.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )
    std_se_u_df = pd.DataFrame(
        np.std(std_u_store, axis=0).reshape([len(TYPE), len(SHAREABLE)]).T, 
        index=NAME_w, 
        columns=[f'std.se.upper.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )

    std_scale_se_s = pd.DataFrame(
        np.std(std_scale_s_store, axis=0).reshape([1, len(TYPE)]), 
        index=['s'], 
        columns=[f'scale.std.se.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )
    std_scale_se_sm = pd.DataFrame(
        np.std(std_scale_sm_store, axis=0).reshape([1, len(TYPE)]), 
        index=['sm'], 
        columns=[f'scale.std.se.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )
    std_scale_se_sf = pd.DataFrame(
        np.std(std_scale_sf_store, axis=0).reshape([1, len(TYPE)]), 
        index=['sf'], 
        columns=[f'scale.std.se.h{i}' for i in range(len(TYPE))], 
        dtype=float
        )


    # Print results.
    print("covariance matrix standard errors, unadjusted")
    np.set_printoptions(precision=4, suppress=True)
    for i in range(len(TYPE)):
        print(f'h{i+1}')
        print(cov_matrix_se[:, :, i])

    print("covariance matrix standard errors, adjusted")
    for i in range(len(TYPE)):
        print(f'h{i+1}')
        print(cov_matrix_pd_se[:, :, i])

    print("correlation matrix standard errors")
    for i in range(len(TYPE)):
        print(f'h{i+1}')
        print(cor_matrix_se[:, :, i])

    print("standard deviation standard errors")
    print(std_se_df.astype(float).round(4))

    print("LASSO robustness check, 0.5*penalty")
    print(std_se_l_df.astype(float).round(4))

    print("LASSO robustness check, 2*penalty")
    print(std_se_u_df.astype(float).round(4))

    print("scale index standard deviation standard errors, singles")
    print(std_scale_se_s.astype(float).round(4))

    print("scale index standard deviation standard errors, single males")
    print(std_scale_se_sm.astype(float).round(4))

    print("scale index standard deviation standard errors, singles females")
    print(std_scale_se_sf.astype(float).round(4))