from sklearn.base import TransformerMixin, BaseEstimator
from sklearn.preprocessing import LabelEncoder, MinMaxScaler
from sklearn.impute import SimpleImputer
import sys
sys.path.append("/home/ec2-user/TCM")
from automl.dev_tools import series_to_supervised
import pandas as pd
import hdbscan
import numpy as np
import networkx
import os
import multiprocessing as mp
from sklearn.feature_selection import chi2
# for stop seeing unnecessary messages
pd.options.mode.chained_assignment = None
os.system("taskset -p 0xff %d" % os.getpid())
import logging
# from sklearn.preprocessing import Imputer


logging.basicConfig(format='%(asctime)s     %(levelname)-8s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    stream=sys.stdout,
                    level='INFO')

def init(l, df):
    """
    used to prevent race conditions
    :param l: the lock object to use for sync
    :return: None
    """
    global lock, df_temp
    lock = l
    df_temp = df


def init_time_series(df):
    """
    used to prevent race conditions
    :param l: the lock object to use for sync
    :return: None
    """
    global df_temp
    df_temp = df


def try_this(x):

    doing(*x)


def doing(key, key_field, date_field, fill_end):

    dir_path = os.path.dirname(os.path.realpath(__file__))
    X_t = df_temp[df_temp[key_field] == key].sort_values(by=date_field)
    X_t = X_t.fillna(method="ffill").fillna(method="bfill").fillna(fill_end)
    lock.acquire()
    if not os.path.exists(dir_path + "/data.csv"):
        X_t.to_csv(dir_path + "/data.csv")
    else:
        X_t.to_csv(dir_path + "/data.csv", mode="a", header=False)
    lock.release()


class CustomTransformer(BaseEstimator, TransformerMixin):
    """
    a general class for creating a machine learning step in the machine learning pipeline
    """
    def __init__(self):
        """
        constructor
        """
        super(CustomTransformer, self).__init__()

    def fit(self, X, y=None, **kwargs):
        """
        an abstract method that is used to fit the step and to learn by examples
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self: the class object - an instance of the transformer - Transformer
        """
        pass

    def transform(self, X, y=None, **kwargs):
        """
        an abstract method that is used to transform according to what happend in the fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X: the transformed data - Dataframe
        """
        pass

    def fit_transform(self, X, y=None, **kwargs):
        """
        perform fit and transform over the data
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X: the transformed data - Dataframe
        """
        self = self.fit(X, y)
        return self.transform(X, y)


class ClearNoCategoriesTransformer(CustomTransformer):
    """
    transformer that remove categorical features with no variance
    """
    def __init__(self, categorical_cols=[]):
        """
        constructor
        :param categorical_cols: the categoric columns to transform - list
        """
        super(ClearNoCategoriesTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.remove = []
        self._columns = None

    def _clear(self, df, col):
        """
        check if we have more than one level in a categorical feature and removes it if not
        :param df: the Dataframe to check - Dataframe
        :param col: the column to check in the Dataframe - string
        """
        if df[col].unique().shape[0] == 1:
            self.remove.append(col)

    def fit(self, X, y=None, **kwargs):
        """
        learns which features need to be removed
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self: the class object - an instance of the transformer - Transformer
        """
        # remember the origianl features
        self._columns = X.columns
        try:
            df = X.copy(True)
        except Exception as e:
            raise e
        try:
            if len(self.categorical_cols) > 0:
                cols = self.categorical_cols
            else:
                cols = df.columns
            for col in cols:
                self._clear(df, col)
        except Exception as e:
            logging.info(e)
        logging.info("ClearNoCategoriesTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        removes all the features that were found as neede to be removed in the fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X[columns]: the transformed data with the chosen columns- Dataframe
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X, columns=self._columns, copy=True)
        columns = [col for col in X.columns if col not in self.remove]
        logging.info("ClearNoCategoriesTransformer transform end")
        return X[columns]


class ImputeTransformer(CustomTransformer):
    """
    transformer that deals with missing values for each column passed by transforming them and adding a new indicator
    column that indicates which value was imputed
    """
    def __init__(self, numerical_cols=[], categorical_cols=[], strategy='negative', key_field=None, date_field=None, fill_end=-1, parallel=False):
        """
        constructor
        :param numerical_cols: the numerical columns to check for missing values over - list
        :param categorical_cols: the categorical columns, used to save the new indicator feature in this list - list
        :param strategy: the way to deal with missing value, default: "zero" - transforming all of them to zero - string
        """
        super(ImputeTransformer, self).__init__()
        self.strategy = strategy
        self.imp = None
        self.statistics_ = None
        self.indicators = None
        self.key_field = key_field
        self.date_field = date_field
        self.numerical_cols = numerical_cols
        self.categorical_cols = categorical_cols
        self.fill_end = fill_end
        self.parallel = parallel

    def fit(self, X, y=None, **kwargs):
        """
        learns what are the column that have missing values, to make sure that in the transform the data that will
        passed in will have does feature and if not they will be created
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self: the class object - an instance of the transformer - Transformer
        """
        self.numerical_cols = [col for col in self.numerical_cols if col in X.columns]
        self.categorical_cols = [col for col in self.categorical_cols if col in X.columns]
        if self.strategy not in ["time_series", "zero"]:
            self.imp = SimpleImputer(strategy=self.strategy)
            self.imp.fit(X[self.numerical_cols])
            self.statistics_ = pd.Series(self.imp.statistics_, index=X[self.numerical_cols].columns)
        logging.info("ImputeTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        replacing the missing value by the strategy parameter sent in the fit method, we only imputing numerical
        features, the categorical features will put all the missing values to a new level
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X: the transformed data - Dataframe
        """
        dir_path = os.path.dirname(os.path.realpath(__file__))
        for col in self.numerical_cols:
            nulls = X[col].isnull()
            missing_ind_name = col + '_was_missing'
            if sum(nulls) > 0:
                self.categorical_cols.append(missing_ind_name)
                X[missing_ind_name] = nulls.values
                if self.strategy == "zero":
                    X[col].fillna(0, inplace=True)
                if self.strategy == "negative":
                    X[col].fillna(-9999999999999, inplace=True)
        if self.strategy == "time_series":
            X = X.reset_index()
            keys = X[self.key_field].unique().tolist()
            if self.parallel:
                if os.path.exists(dir_path + "/data.csv"):
                    os.remove(dir_path + "/data.csv")
                lock = mp.Lock()
                pool = mp.Pool(mp.cpu_count(), initializer=init, initargs=(lock, X.copy(True)))
                pool.map_async(try_this, [(key, self.key_field, self.date_field, self.fill_end) for key in keys])
                pool.close()
                pool.join()
            else:
                try:
                    if os.path.exists(dir_path + "/data.csv"):
                        os.remove(dir_path + "/data.csv")
                except Exception as e:
                    pass
                for key in keys:
                    X_t = X[X[self.key_field] == key].sort_values(by=self.date_field)
                    X_t = X_t.fillna(method="ffill").fillna(method="bfill").fillna(self.fill_end)
                    if not os.path.exists(dir_path + "/data.csv"):
                        X_t.to_csv(dir_path + "/data.csv")
                    else:
                        X_t.to_csv(dir_path + "/data.csv", mode="a", header=False)
            try:
                X = pd.read_csv(dir_path + "/data.csv", low_memory=False)
            except Exception as e:
                os.remove(dir_path + "/data.csv")
                raise e
            os.remove(dir_path + "/data.csv")
            X = X.sort_values(by='Unnamed: 0').drop('Unnamed: 0', axis=1).reset_index(drop=True)
            X = X.set_index([self.key_field, self.date_field])

        elif self.strategy not in ["zero", "time_series"]:
            Ximp = self.imp.transform(X[self.numerical_cols])
            Xfilled = pd.DataFrame(Ximp, index=X[self.numerical_cols].index, columns=X[self.numerical_cols].columns)
            X[self.numerical_cols] = Xfilled
        logging.info("ImputeTransformer transform end")
        return X


class OutliersTransformer(CustomTransformer):
    """
    transformer that deals with outliers values for each column passed by transforming them and adding a new indicator
    column that indicates which value in the column was an outlier
    """
    def __init__(self, min_samples=None, epsilon=None, strategy="iqr", upper_q=0.999, lower_q=0.001,
                 increment=0.001, numerical_cols=[], categorical_cols=[], magnitude=3.5):
        """
        constructor
        :param min_samples: The number of samples (or total weight) in a neighborhood for a point to be considered as
        a core point. This includes the point itself - int
        :param epsilon: The maximum distance between two samples for them to be considered as in the same neighborhood -
        float
        :param strategy: the strategy to use in otrder to deal with the outliers detected - string
        :param upper_q: the upper percentile to transform the high outlier to, default: 0.999 - float
        :param lower_q: the lower percentile to transform the low outlier to, default: 0.001 - float
        :param increment: the value to lower the upper_q or to upper the lower_q to avoid transforming to -inf to inf -
        float
        :param numerical_cols: the numerical columns to check for outliers - list of string
        :param categorical_cols: the categorical columns to add the names of the indicator columns created - list of
        string
        """
        super(OutliersTransformer, self).__init__()
        self.strategy = strategy
        self.epsilon = epsilon
        self.min_samples = min_samples
        self.lower_q = lower_q
        self.upper_q = upper_q
        self.increment = increment
        self.numerical_cols = numerical_cols
        self.categorical_cols = categorical_cols
        self.outliers = None
        self.cols_borders = dict()
        self.magnitude = magnitude

    @staticmethod
    def _fix_inside(x, low, high):
        """
        check to see if a value dosen't pass the limits
        :param x: the value to check - float
        :param low: the lower limit - float
        :param high: the upper limit - float
        :return: x: the value checked, if we passed the limit the limit, else the value - float
        """
        if x < low:
            return low
        if x > high:
            return high
        return x

    def _fix(self, X, col):
        """
        dealing with the outliers by finding the best valid value that is closed to the upper_q and lower_q
        :param X: the datafarame - Dataframe
        :param col: the column to fix - string
        :return: X: the dataframe with fixed column - Dataframe
        """
        low = X[col].quantile(self.lower_q)
        high = X[col].quantile(self.upper_q)

        # iterating incrementally until finding a valid value
        while low == float("-inf"):
            self.lower_q += self.increment
            low = X[col].quantile(self.lower_q)

        while high == float("inf"):
            self.upper_q -= self.increment
            high = X[col].quantile(self.upper_q)

        X[col] = X[col].apply(lambda x: self._fix_inside(x, low, high))

        return X

    def _hdbscan(self, X, col):
        """
        using the dbscan clustering to detect outliers: https://en.wikipedia.org/wiki/dbscan
        :param X: the dataframe - Dataframe
        :param col: the column to check and fix - string
        :return: dbscan: instance of dbscan class implemented:
        https://scikit-learn.org/stable/modules/generated/sklearn.cluster.dbscan.html
        """
        if self.min_samples is None:
            self.min_samples = int(X[col].shape[0] * 0.01)

        if self.epsilon is None:
            self.epsilon = np.max([1.0, X[col].median() + X[col].std()])
        try:
            return hdbscan.HDBSCAN(min_cluster_size=self.min_samples, min_samples=2, cluster_selection_method="leaf",
                                   allow_single_cluster=True, core_dist_n_jobs=mp.cpu_count(), algorithm="boruvka_kdtree")
        except Exception as e:
            return None

    def _unpack_outliers(self, args):

        self.outliers_run(*args)

    def outliers_run(self, col, dbscan, X):
        try:
            outlier_scores = dbscan.fit(X[col].values.reshape(-1, 1)).outlier_scores_
            threshold = pd.Series(outlier_scores).quantile(0.99)
            labels = [-1 if x > threshold else 0 for x in outlier_scores]
            index_of_outliers = np.where(outlier_scores > threshold)[0].tolist()
            try:
                # index_of_outliers = X[col][outliers == True].index.tolist()
                if sum(index_of_outliers) > 0:
                    X_nulls = X.copy(True)
                    X_nulls[col].iloc[index_of_outliers] = None
                    nulls = X_nulls[col].isnull()
                    outlier_ind_name = col + '_has_outliers'
                    # if there are missing values create a new indicator
                    if sum(nulls) > 0:
                        self.categorical_cols.append(outlier_ind_name)
                        X[outlier_ind_name] = labels == -1
                    if self.strategy == "zero":
                        X[col].iloc[index_of_outliers] = 0
                    else:
                        X = self._fix(X, col)
                return col, X[col]
            except Exception as e:
                logging.info(e)
                return None
        except Exception as e:
            logging.info("there is an error in dbscan:")
            logging.info(e)
            return None

    def fit(self, X, y=None, **kwargs):
        """
        identify outliers by using the dbscan clustering algorithm, and we deal with them by strategy
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        if self.strategy == "iqr":
            cols_to_iterate = [col for col in self.numerical_cols if col in X.columns]
            for col in cols_to_iterate:
                descriptive = X[col].dropna().describe()
                try:
                    iqr = (descriptive["75%"] - descriptive["25%"])
                except Exception as e:
                    logging.info(e)
                self.cols_borders[col] = dict(min_v=-self.magnitude * iqr + descriptive["50%"],
                                              max_v=self.magnitude * iqr + descriptive["50%"])
            logging.info("OutliersTransformer fit end")
            return self
        else:
            self.numerical_cols = [col for col in self.numerical_cols if col in X.columns]
            self.categorical_cols = [col for col in self.categorical_cols if col in X.columns]
            self.outliers = {col: self._hdbscan(X, col) for col in self.numerical_cols}
            logging.info("OutliersTransformer fit end")

    def _iqr(self, x, min_v, max_v, col):

        if x > self.cols_borders[col]["max_v"]:
            return max_v
        elif x < self.cols_borders[col]["min_v"]:
            return min_v
        else:
            return x

    def transform(self, X, y=None, **kwargs):
        """
        used the learned behaviour in the fit method to transform the columns in the datafarme
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X: the transformed data - Dataframe
        """
        # X = X.copy(True)
        for col in self.numerical_cols:
            m = X.loc[X[col] != np.inf, col].max() + X[col].std()
            X[col].replace(np.inf, m, inplace=True)
            m = X.loc[X[col] != np.NINF, col].min() + X[col].std()
            X[col].replace(np.NINF, m, inplace=True)
        if self.strategy == "iqr":
            cols_to_iterate = [col for col in self.numerical_cols if col in X.columns]
            for col in cols_to_iterate:
                col_df = X[(X[col] > self.cols_borders[col]["min_v"]) & (X[col] < self.cols_borders[col]["max_v"])][col].dropna()
                if col_df.shape[0] != 0:
                    min_v = col_df.min()
                    max_v = col_df.max()
                    X[col] = X[col].apply(lambda x: self._iqr(x, min_v, max_v, col))
        else:
            items = [self._unpack_outliers((col, dbscan)) for col, dbscan in self.outliers.items()]
            pool = mp.Pool(mp.cpu_count())
            results = pool.map_async(items, self._unpack_outliers).get()
            results = [self._unpack_outliers(x) for x in items]
            results = [self._unpack_outliers((col, dbscan, X)) for col, dbscan in self.outliers.items()]
            for result in results:
                if result is not None:
                    X[result["col"]] = result["col_df"]
        logging.info("OutliersTransformer transform end")
        return X


class ScalingTransformer(CustomTransformer):
    """
    Transformer that performs scaling for continuous features
    """
    def __init__(self, numerical_cols=[]):
        """
        constructor
        :param numerical_cols: the numerical columns in the data to scale
        """
        super(ScalingTransformer, self).__init__()
        self.numerical_cols = numerical_cols
        self.columns = None
        self.scaler = None

    def fit(self, X, y=None, **kwargs):
        """
        learns how to scale
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        cols = [col for col in self.numerical_cols if col in X.columns]
        if len(cols) > 0:
            self.scaler = MinMaxScaler()
            self.scaler.fit(X[cols])
        self.columns = X.columns
        logging.info("ScalingTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        ferforms scaling using standartization
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        cols_to_complete = [col for col in self.columns if col not in X.columns]
        df = X.copy(True)
        for col in cols_to_complete:
            df[col] = 0
        cols = [col for col in self.numerical_cols if col in df.columns]
        if len(cols) > 0:
            df[cols] = self.scaler.transform(X[cols])
        logging.info("ScalingTransformer transform end")
        return df


class CategorizingTransformer(CustomTransformer):
    """
    transformer that adds multiple categories of the same column together
    """
    def __init__(self, categorical_cols=[], threshold=0.8):
        """
        constructor
        :param categorical_cols: list of names of column to categorize - list of strings
        :param threshold: the threshold that deside to take only the columns that contains at list the threshold percent
        from all the values in the column
        """
        super(CategorizingTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.threshold = threshold

    def _frequency_table(self, df, col, ind=True, transform=True, cut=True):
        """
        calculate the frequnecy table of values for a specific column in a dataframe
        :param df: the dataframe to take the column from - Dataframe
        :param col: the column to calculate - string
        :param ind: to create a indicator of which value percent is not above the threshold - boolean
        :param transform: to transform or not - boolean
        :param cut: to cut the dataframe and leave only the samples that have a class that passed the default value -
        boolean
        :return: df_return: the transformed dataframe in the column specified - Dataframe
        """
        value_counts = df[col].astype(str).str.lower().value_counts().to_frame()
        df_return = value_counts.sort_values(col, ascending=False)
        summer = df_return[col].sum()
        df_return = df_return.reset_index()
        columns = df_return.columns.values.tolist()
        columns[1] = "counts"
        columns[0] = col
        df_return.columns = columns
        df_return["per"] = df_return["counts"].apply(lambda count: float("{0:.5f}".format(count / summer)))
        df_return["accumulate"] = df_return["per"].cumsum()
        df_return["accumulate"] = df_return["accumulate"].apply(lambda x: float("{0:.2f}".format(x)))

        if ind:
            df_return["above_threshold"] = df_return.apply(lambda x: 1 if x["accumulate"] < self.threshold else 0,
                                                           axis=1)
            ind = df_return[df_return["above_threshold"] == 0].index
            if len(ind) != 0:
                ind = ind[0]
            df_return["above_threshold"].iloc[ind] = 1

        if transform and cut:
            df_return[col] = df_return.apply(lambda x: "joined_category" if x["above_threshold"] == 0 else x[col],
                                             axis=1)
        else:
            df_return = df_return[df_return["above_threshold"] == 1]

        return df_return

    @staticmethod
    def _change(x, temp, frq_test, col):
        """
        changing the values
        :param x: the value to change - string
        :param temp: the temporary dictionary to use for tansforming - dictionary
        :param frq_test: the frequnecy table created with _frequency_table method - Dataframe
        :param col: the column to check for values in - string
        :return: the class or "other" if it was merged - string
        """
        if str(x).lower() in frq_test[col].tolist():
            temp[x] = x
            return x
        else:
            temp[x] = "other"
            return "other"

    def fit(self, X, y=None, **kwargs):
        """
        learning from the X dataframe what class to keep in which to join together in each column specified
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        logging.info("CategorizingTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        for each column specified in categorical_cols list we join values if needed by the learning in fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: X: the transformed data - Dataframe
        """
        X_copy = X.copy(True)
        cat_dic = {}
        temp = {}
        columns = X_copy.columns

        for col in self.categorical_cols:
            if col not in columns:
                X_copy[col] = "other"
            else:
                frq_test = self._frequency_table(X, col, True, False, False)
                X_copy[col] = X[col].apply(lambda x: self._change(x, temp, frq_test, col))
                cat_dic[col] = temp
                temp = {}

        self.categorical_cols = cat_dic
        logging.info("CategorizingTransformer transform end")
        return X_copy


class CategorizeByTargetTransformer(CustomTransformer):
    """
    transformer that adds multiple categories of the same column together by the disribution of each class in the column
    with the target column
    """
    def __init__(self, categorical_cols=[], uniques=10, threshold=0.02):
        """
        constructor
        :param categorical_cols: the categorical columns to check for joinable classes - list of strings
        :param uniques: the max number of classes allowed in the column, default: 10 - int
        :param threshold: the max diff in distribution to join classes, default: 0.02 -float
        """
        super(CategorizeByTargetTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.uniques = uniques
        self.threshold = threshold
        self.names = dict()

    @staticmethod
    def _check_if_could_joined(df, col, y):
        """
        creating a distribution of column with the target column
        :param df: the dataframe to check - Dataframe
        :param col: the column to check - string
        :param y: the target column to compare with - Series
        :return: the distribution table of the column and target column
        """
        X = df[col].values
        try:
            if isinstance(y, pd.Series):
                target = y.values
            elif isinstance(y, pd.DataFrame):
                target = y.iloc[:, 0].values
            else:
                raise Exception("y is not a DataFrame or Series, please pass y typed Series to the function")
        except Exception as e:
            logging.info(e)
        try:
            re = pd.crosstab(X, target, rownames=['X'], colnames=['target'], margins=True)
        except Exception as e:
            return pd.crosstab(X, target, rownames=['X'], colnames=['target'])
        try:
            re[True]
        except Exception as e:
            re[True] = 0
        re["per"] = re[True] / re["All"]
        re.drop("All", axis=0, inplace=True)
        re.drop("All", axis=1, inplace=True)

        return re

    def fit(self, X, y=None, **kwargs):
        """
        learning which columns needs transformations and which classes needs to be joined together according to the
        distribution in each target class
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        df = X.copy(True)
        df[self.categorical_cols].fillna('nan', inplace=True)

        for col in self.categorical_cols:
            m = df.loc[df[col] != np.inf, col].max() + df[col].std()
            df[col].replace(np.inf, m, inplace=True)
            m = df.loc[df[col] != np.NINF, col].min() + df[col].std()
            df[col].replace(np.NINF, m, inplace=True)
            re = self._check_if_could_joined(df, col, y)
            all_cat = []

            if len(re.index.tolist()) >= self.uniques:

                for row in re.index:
                    try:
                        per = re.loc[row]["per"]
                    except Exception as e:
                        per = re.loc[str(row)]["per"]
                    try:
                        pe = re.loc[row:]
                    except Exception as e:
                        pe = re.iloc[int(row):]

                    for row2 in pe.index:
                        if row2 != row and np.abs(re.loc[row2]["per"] - per) <= self.threshold:
                            all_cat.append((row, row2))
                # using a graph to find the combined categories in a column and create a new joined one
                g = networkx.Graph(all_cat)
                columns = re.T.columns.tolist()
                drop_out = []
                self.names[col] = {}
                for c in networkx.connected_components(g):
                    subgraph = g.subgraph(c)
                    category = '-'.join([str(x) for x in subgraph.nodes()])
                    for node in subgraph.nodes():
                        self.names[col][str(node).replace(" ", "")] = category
                    drop_out += subgraph.nodes()
                left_cols = [col for col in columns if col not in drop_out]
                for colu in left_cols:
                    self.names[col][colu] = colu
        logging.info("CategorizeByTargetTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        joining categories in columns according to what is learned in fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                "_has_outliers" in col]
        df = X.copy()
        df[cols].fillna('nan', inplace=True)
        for col in self.names.keys():
            keys = self.names[col].keys()
            df[col] = df[col].apply(lambda x: self.names[col][str(x).replace(" ", "")] if str(x).replace(" ", "") in
                                                                                          keys else x)
        logging.info("CategorizeByTargetTransformer transform end")
        return df


class CorrelationTransformer(CustomTransformer):
    """
    feature selection transformer that checks correlations between columns and the target column using the spearman
    correlation method
    """
    def __init__(self, numerical_cols=[], categorical_cols=[], target=None, threshold=0.7):
        """
        constructor
        :param numerical_cols: the numerical columns in the dataframe - list of strings
        :param categorical_cols: the categorical columns in the dataframe - list of strings
        :param target: the name of the target column, default: None - list of string
        :param threshold: the threshold that indicates if the columns are correlated or not, default: 0.7 - float
        """
        super(CorrelationTransformer, self).__init__()
        self.numerical_cols = numerical_cols
        self.categorical_cols = categorical_cols
        self.target = target
        self.threshold = threshold
        self.columns_stay = None
        self.fit_first = True

    def _remove_correlated_features(self, corr):
        """
        checks and deals with correlated columns
        :param corr: the correlation matrix of all the columns - Dataframe
        :return: cols2: the columns to keep - list of strings
        """
        cols2 = corr.drop(self.target, axis=1).columns.tolist()
        checked = []

        for col in cols2:
            cols3 = [column for column in cols2 if column not in checked + [col]]
            for col2 in cols3:
                if corr.loc[col, col2] >= self.threshold:
                    if abs(corr.loc[col][self.target].values[0]) > abs(corr.loc[col2][self.target].values[0]):
                        cols2.remove(col2)
                    else:
                        cols2.remove(col)
                        break

            checked.append(col)

        return cols2

    def fit(self, X, y=None, **kwargs):
        """
        learn which columns to keep
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        logging.info("CorrelationTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        keeping only the columns that were learned in the fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        if self.fit_first:
            model_cols = self.categorical_cols + self.numerical_cols + self.target
            df = pd.concat([X.reset_index(drop=True), y.reset_index(drop=True)], axis=1)
            final_cols = [col for col in model_cols if col in df.columns]
            corr = df[final_cols].corr("spearman")
            if self.target[0] not in corr.columns:
                logging.info("target not in correlation")
                logging.info("CorrelationTransformer transform end")
                return X
            try:
                stayed = self._remove_correlated_features(corr)
            except Exception as e:
                logging.info("can't correlate this")
                logging.info("this is the exception")
                stayed = corr.columns
                logging.info(e)
            self.columns_stay = [col for col in stayed if col in df.columns]
            self.numerical_cols = [col for col in self.numerical_cols if col in self.columns_stay]
            self.categorical_cols = [col for col in self.categorical_cols if col in self.columns_stay]
            self.fit_first = False
        cols = [col for col in self.columns_stay if col in X.columns]
        if len(cols) == 0:
            logging.info("CorrelationTransformer transform end")
            logging.info("can't remove features, only few remained")
            return X
        else:
            logging.info("CorrelationTransformer transform end")
            return X[cols]


class ChiSquareTransformer(CustomTransformer):
    """
    feature selection transformer that checks correlations between columns and the target column using the spearman
    correlation method
    """
    def __init__(self, categorical_cols=[], numerical_cols=[], threshold=None, alpha=0.05, y_threshold=10):
        """
        constructor
        :param numerical_cols: the numerical columns in the dataframe - list of strings
        :param categorical_cols: the categorical columns in the dataframe - list of strings
        :param target: the name of the target column, default: None - list of string
        :param threshold: the threshold that indicates if the columns are correlated or not, default: 0.7 - float
        """
        super(ChiSquareTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.numerical_cols = numerical_cols
        self.threshold = threshold
        self.alpha = alpha
        self.columns_stay = None
        self.y_threshold = y_threshold

    def fit(self, X, y=None, **kwargs):
        """
        learn which columns to keep
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        try:
            if y.unique().shape[0] > self.y_threshold:
                logging.info("too much values in target")
                logging.info("ChiSquareTransformer fit end")
                return self
        except Exception as e:
            logging.info("ChiSquareTransformer fit end")
            return self
        try:
            chi_scores = chi2(X[self.categorical_cols], y)
        except Exception as e:
            logging.info("ChiSquareTransformer fit end")
            return self
        p_values = pd.Series(chi_scores[1], index=self.categorical_cols)
        p_values.sort_values(ascending=True, inplace=True)
        chi_squers = pd.Series(chi_scores[0], index=self.categorical_cols)
        chi_squers.sort_values(ascending=False, inplace=True)
        if self.threshold is not None:
            self.columns_stay = list(chi_squers[:int(chi_squers.shape[0] * self.threshold)].index)
        else:
            self.columns_stay = list(chi_squers[p_values.apply(lambda x: x <= self.alpha).values == True].index)
        self.columns_stay += self.numerical_cols
        logging.info("ChiSquareTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        keeping only the columns that were learned in the fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        try:
            cols = [col for col in self.columns_stay if col in X.columns]
            if len(cols) == 0:
                logging.info("can't remove features, only few remained")
                logging.info("ChiSquareTransformer transform end")
                return X
            else:
                logging.info("ChiSquareTransformer transform end")
                return X[cols]
        except Exception as e:
            logging.info("ChiSquareTransformer transform end")
            return X


class LabelEncoderTransformer(CustomTransformer):
    """
    transformer that is used for labeling the categorical columns to a number instead of string
    """
    def __init__(self, categorical_cols=[]):
        """
        constructor
        :param categorical_cols: the categorical columns to label - list of strings
        """
        super(LabelEncoderTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.labels = {}
        self._columns = None

    def fit(self, X, y=None, **kwargs):
        """
        learning the labeling of each category for each column in a categorical column
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self._columns = X.columns
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        cols = [col for col in self.categorical_cols if col in X.columns]
        self.labels = {col: {"labeler": LabelEncoder().fit(X[col].astype("str")),
                             "uniques": X[col].astype("str").unique()} for col in cols}
        logging.info("LabelEncoderTransformer fit end")
        return self

    def _labeler(self, col):
        """
        changing the values to numbers
        :param col: the column to label
        :return: col: a Series of labled data - Series
        """
        # todo change this from returning 0 to something else, it needs to deal with values that are not in the labeler
        return col.apply(lambda value: self.labels[col.name]["labeler"]. \
                         transform([str(value)])[0] if str(value) in self.labels[col.name]["uniques"] else 0)

    def transform(self, X, y=None, **kwargs):
        """
        labeling the columns according to the lables learned in the fit method
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X, columns=self._columns, copy=True)
        cols = [col for col in self.categorical_cols if col in X.columns]
        X.loc[:, cols] = X[cols].apply(lambda col: self._labeler(col), axis=0)
        logging.info("LabelEncoderTransformer transform end")
        return X


class DummiesTransformer(CustomTransformer):
    """
    Transformer that performs one hot encoding to the categorical columns, and drops one category as a base category
    """
    def __init__(self, categorical_cols=[]):
        """
        constructor
        :param categorical_cols: the categorical columns in the data, to one hot encode
        """
        super(DummiesTransformer, self).__init__()
        self.categorical_cols = categorical_cols
        self.cols_name_after = None
        self.final_cols = None

    def fit(self, X, y=None, **kwargs):
        """
        learns what are columns to create dummy vareiables for each column in the categorical data
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        self.categorical_cols = [col for col in X.columns if col in self.categorical_cols or "_was_missing" in col or
                                 "_has_outliers" in col]
        df = X.copy(True)
        try:
            df = pd.concat([df.drop(self.categorical_cols, axis=1), pd.get_dummies(data=df[self.categorical_cols],
                                                                                   columns=self.categorical_cols,
                                                                                   drop_first=True)], axis=1)
        except Exception as e:
            logging.info(e)
        self.cols_name_after = df.columns
        logging.info("DummiesTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        transform each category for each categorical column to a new dummy column
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        cols = [col for col in self.categorical_cols if col in X.columns]
        df = X.copy(True)
        if len(cols) > 0:
            df = pd.concat([df.drop(cols, axis=1),
                            pd.get_dummies(data=df[cols], columns=cols, drop_first=True)], axis=1)
            cols_out = [col for col in df.columns if col in self.cols_name_after]
            cols_complete = [col for col in self.cols_name_after if col not in df.columns]
            df = df[cols_out]
            for col in cols_complete:
                df[col] = 0
            if self.final_cols is None:
                self.final_cols = cols_out + cols_complete
            logging.info("DummiesTransformer transform end")
            return df[self.final_cols]
        else:
            logging.info("DummiesTransformer transform end")
            return df


def time_series_parallel_unpack(args):
    """

    :param args:
    :return:
    """
    return time_series_parallel(*args)


def time_series_parallel(df_name, key, date, target, static_cols, w, r):
    """

    :param df_name:
    :param key:
    :param date:
    :param target:
    :param static_cols:
    :param w:
    :param r:
    :return:
    """
    dff = df_temp[df_temp[key] == df_name].sort_values(by=date, ascending=True)
    df_t = series_to_supervised(dff.drop([key, date], axis=1), target=target, static_cols=static_cols, w=w, r=r,
                                dropnan=False)
    df_t[key] = dff[key]
    df_t[date] = dff[date]
    return df_t


class TimeSeriesTransformer(CustomTransformer):
    """
    Transformer that performs time series data preparation
    """
    def __init__(self, w=5, r=1, dropnan=True, target=None, method="window", key=None, date=None, split_y=False,
                 static_cols=[], **kwargs):
        """

        :param w:
        :param r:
        :param dropnan:
        :param target:
        :param method:
        :param key:
        :param date:
        :param split_y:
        :param static_cols:
        :param kwargs:
        """
        super(TimeSeriesTransformer, self).__init__()
        self.w = w
        self.r = r
        self.dropnan = dropnan
        self.target = target
        self.method = method
        self.key = key
        self.date = date
        self.split_y = split_y
        self.static_cols = static_cols
        self.kwargs = kwargs

    def fit(self, X, y=None, **kwargs):
        """
        return self no need to fit
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        logging.info("TimeSeriesTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        transform the data to the time series data
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        df = pd.concat([X, y], axis=1)
        if self.key not in df.columns and self.date not in df.columns:
            df = df.reset_index()
        # if self.date not in df.columns:
            # df[self.date] = self.kwargs["date_col"]
        if self.method == "window":
            df_names = df[self.key].unique()
            pool = mp.Pool(mp.cpu_count(), initializer=init_time_series, initargs=(df,))
            dfs = pool.map_async(time_series_parallel_unpack, [(df_name, self.key, self.date, self.target, self.static_cols, self.w, self.r) for df_name in df_names]).get()
            pool.close()
            pool.join()
            df = pd.concat(dfs)
            df.set_index([self.key, self.date], inplace=True)
            logging.info("TimeSeriesTransformer transform end")
            return df


class FeatureSelectionTransformer(CustomTransformer):
    """
    Transformer that performs time series data preparation
    """
    def __init__(self, target=None, top_n=100, keys=[], problem_type="classification"):
        """
        :param target:
        :param method:
        :param key:
        :param date:
        """
        super(FeatureSelectionTransformer, self).__init__()
        self.target = target
        self.top_n = top_n
        self.keys = keys
        self.problem_type = problem_type
        self.features = []


    def fit(self, X, y=None, **kwargs):

        """
        return self no need to fit
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: self - the class object - an instance of the transformer - Transformer
        """
        from xgboost import XGBRegressor, XGBClassifier
        if self.problem_type == "classification":
            xgb = XGBClassifier()
        else:
            xgb = XGBRegressor()
        xgb.fit(X, y)
        features = list(X.columns)
        importances = xgb.feature_importances_
        indices = np.argsort(importances)[-self.top_n:]
        indices = indices[::-1]
        df = pd.DataFrame([(a, b) for a, b in zip(importances[indices], [features[i] for i in indices])],
                          columns=["importance", "feature"])
        self.features = df["feature"].tolist()
        logging.info("FeatureSelectionTransformer fit end")
        return self

    def transform(self, X, y=None, **kwargs):
        """
        transform the data to the time series data
        :param X: features - Dataframe
        :param y: target vector - Series
        :param kwargs: free parameters - dictionary
        :return: df: the transformed data - Dataframe
        """
        logging.info("FeatureSelectionTransformer transform end")
        return X[self.features]
