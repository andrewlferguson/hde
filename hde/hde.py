
import numpy as np 

from keras import backend as K
from keras.models import Model
from keras.optimizers import Adam
import keras.layers as layers

from sklearn.base import BaseEstimator, TransformerMixin

__all__ = ['HDE']


def create_encoder(input_size, output_size, hidden_layer_depth, 
            hidden_size, activation):
    encoder_input = layers.Input(shape=(input_size,))
    encoder = layers.Dense(hidden_size, activation=activation)(encoder_input)
    for _ in range(hidden_layer_depth - 1):
        encoder = layers.Dense(hidden_size, activation=activation)(encoder)
    
    encoder = layers.Dense(output_size, activation=activation)(encoder)
    model = Model(encoder_input, encoder)
    return model


def create_hde(encoder, input_size):
    input_t0 = layers.Input(shape=(input_size,))
    input_tt = layers.Input(shape=(input_size,))
    z_t0 = encoder(input_t0)
    z_tt = encoder(input_tt)
    z = layers.Concatenate(axis=1)([z_t0, z_tt])
    hde = Model([input_t0, input_tt], z)
    return hde


def create_orthogonal_encoder(encoder, input_size, n_components, means, gs_matrix, norms): 
    
    def layer(x, n_components=n_components, means=means, gs_matrix=gs_matrix, norms=norms):
        x -= means
        xs = []
        for i in range(n_components):
            xi = x[:,i]
            for j in range(i):
                xi -= gs_matrix[i, j]*xs[j]
            xs.append(xi)

        xo = K.stack(xs, axis=1)

        xo /= norms
        return xo
    
    inp = layers.Input(shape=(input_size,))
    z = encoder(inp)
    z_orth = layers.Lambda(layer)(z)
    orth_encoder = Model(inp, z_orth)

    return orth_encoder


class HDE(BaseEstimator, TransformerMixin):

    def __init__(self, input_size, n_components=2, lag_time=1, n_epochs=100, 
                 learning_rate=0.001, hidden_layer_depth=2, hidden_size=100, 
                 activation='tanh', batch_size=100, verbose=True):

        self._encoder = create_encoder(input_size, n_components, hidden_layer_depth,
                                      hidden_size, activation)
        self.encoder = self._encoder
        self.hde = create_hde(self._encoder, input_size)

        self.input_size = input_size
        self.n_components = n_components
        self.lag_time = lag_time
        self.n_epochs = n_epochs
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.verbose = verbose

        self.optimizer = Adam(lr=self.learning_rate)

        self.is_fitted = False


    def _corr(self, x, y):
        xc = x - K.mean(x)
        yc = y - K.mean(y)
        corr = K.mean(xc*yc)/(K.std(x)*K.std(y))
        return corr
    

    def _gram_schmidt_empirical(self, v, u):
        return K.mean(u*v, axis=0)/K.mean(u*u, axis=0)*u


    def _loss(self, z_dummy, z):
        loss = 0
        zs = []
        for i in range(self.n_components):
            zi = z[:,i::self.n_components]
            zi -= K.mean(zi)
            for zj in zs:
                zi -= self._gram_schmidt_empirical(zi, zj)
            
            zs.append(zi)
            loss += 1.0/K.log(self._corr(zi[:,0], zi[:,1]))

        return loss


    def _create_dataset(self, data):
        x_t0 = data[:-self.lag_time]
        x_tt = data[self.lag_time:]
        return [x_t0, x_tt]


    def _process_orthogonal_components(self, data):
        self.empirical_means = np.mean(data, axis=0)
        data -= self.empirical_means

        self.scaling_matrix = np.ones((self.n_components, self.n_components))
        for i in range(self.n_components):
            for j in range(i):
                gs_scale = np.mean(data[:,i]*data[:,j], axis=0)/np.mean(data[:,j]*data[:,j], axis=0)
                data[:,i] -= gs_scale*data[:,j]
                self.scaling_matrix[i,j] = gs_scale
                self.scaling_matrix[j,i] = gs_scale

        self.norm_factors = np.sqrt(np.mean(data*data, axis=0))
        return data


    def fit(self, X, y=None):
        train_data = self._create_dataset(X)

        self.hde.compile(optimizer=self.optimizer, loss=self._loss)
        self.hde.fit(train_data, train_data[0], batch_size=self.batch_size, epochs=self.n_epochs)
        
        # Evaluate data and store empirical means, Gram-Schmidt scaling factors, and autocorrelations.
        out = self._encoder.predict(X, batch_size=self.batch_size)
        self._process_orthogonal_components(out)

        self.encoder = create_orthogonal_encoder(
            self._encoder, 
            self.input_size, 
            self.n_components,
            self.empirical_means,
            self.scaling_matrix, 
            self.norm_factors
        )        

        self.is_fitted = True
        return self


    def transform(self, X):

        if self.is_fitted:
            out = self.encoder.predict(X, batch_size=self.batch_size)
            return out
        
        raise RuntimeError('Model needs to be fit first.')


    def fit_transform(self, X, y=None):
        self.fit(X, y)
        return self.transform(X)